"""Root Cause Agent: hypothesis generation (LLM) and hypothesis testing (LLM judgement + deterministic
signal checks + evidence coverage)."""

from __future__ import annotations

import logging
from typing import Any

from veriops.agents.signals import IncidentSignals
from veriops.domain.enums import EvidenceKind, HypothesisCategory, HypothesisStatus
from veriops.domain.models import (
    Alert,
    DocumentCitation,
    EvidenceItem,
    Hypothesis,
    HypothesisTestResult,
    SignalCheck,
)
from veriops.llm.gateway import LLMGateway
from veriops.llm.schemas import HypothesesOutput, HypothesisJudgement, LLMTask
from veriops.shared.errors import LLMError

logger = logging.getLogger(__name__)
E = EvidenceKind
C = HypothesisCategory

REQUIRED_EVIDENCE: dict[HypothesisCategory, frozenset[EvidenceKind]] = {
    C.CAPACITY: frozenset({E.METRICS, E.KUBERNETES}),
    C.CPU_SATURATION: frozenset({E.METRICS, E.KUBERNETES}),
    C.DB_QUERY_REGRESSION: frozenset({E.METRICS, E.DATABASE, E.SLOW_QUERIES, E.DEPLOYMENTS, E.GIT_DIFF}),
    C.CONNECTION_LEAK: frozenset({E.CONNECTION_POOL, E.SLOW_QUERIES, E.DEPLOYMENTS, E.GIT_DIFF}),
    C.MEMORY_LEAK: frozenset({E.METRICS, E.KUBERNETES, E.DEPLOYMENTS}),
    C.DEPENDENCY_OUTAGE: frozenset({E.TRACES, E.DEPENDENCIES}),
    C.CONFIG_REGRESSION: frozenset({E.CONFIG_HISTORY, E.METRICS}),
    C.BAD_DEPLOYMENT: frozenset({E.LOGS, E.DEPLOYMENTS, E.GIT_DIFF}),
    C.UNKNOWN: frozenset(),
}
SUFFICIENT_CONFIDENCE = 0.6
MAX_CONFIDENCE_WITHOUT_VERIFICATION = 0.95


def missing_evidence(category: HypothesisCategory, collected: list[EvidenceKind]) -> list[EvidenceKind]:
    return sorted(REQUIRED_EVIDENCE.get(category, frozenset()) - set(collected))


def signal_checks(category: HypothesisCategory, s: IncidentSignals) -> list[SignalCheck]:
    """Deterministic observations a hypothesis predicts. Shown next to (not merged into) LLM confidence."""
    refs = s.evidence_refs
    checks: list[SignalCheck] = []

    def add(name: str, observed: str, expectation: str, holds: bool, ref: str | None) -> None:
        checks.append(
            SignalCheck(
                name=name,
                observed=observed,
                expectation=expectation,
                holds=holds,
                evidence_ref=refs.get(ref) if ref else None,
            )
        )

    cpu = f"{s.cpu_pct:.0f}%"
    pool = f"{s.db_pool_utilization_pct:.0f}%"
    regression = s.slow_query_max_regression
    match category:
        case C.CAPACITY | C.CPU_SATURATION:
            add("cpu_bound", cpu, "CPU >= 70%", s.cpu_pct >= 70, "metrics")
            add(
                "memory_pressure",
                f"{s.memory_pct:.0f}%",
                "memory >= 80% (if memory-bound)",
                s.memory_pct >= 80,
                "metrics",
            )
            if s.baseline_rps:
                add(
                    "traffic_above_baseline",
                    f"{s.rps:.0f} rps vs {s.baseline_rps:.0f}",
                    "rps > 1.5x baseline",
                    s.rps > 1.5 * s.baseline_rps,
                    "metrics",
                )
            add("pool_headroom", pool, "DB pool < 90%", s.db_pool_utilization_pct < 90, "connection_pool")
        case C.DB_QUERY_REGRESSION:
            add("pool_saturated", pool, "DB pool >= 90%", s.db_pool_utilization_pct >= 90, "connection_pool")
            add(
                "query_regressed",
                f"{regression}x" if regression else "not collected",
                "query mean >= 3x baseline",
                bool(regression and regression >= 3),
                "slow_queries",
            )
            add("cpu_normal", cpu, "app CPU < 70%", s.cpu_pct < 70, "metrics")
            add(
                "onset_after_deployment",
                str(s.deployment_precedes_onset),
                "onset within 30 min after a deployment",
                s.deployment_precedes_onset,
                "deployments",
            )
        case C.CONNECTION_LEAK:
            add("pool_saturated", pool, "DB pool >= 95%", s.db_pool_utilization_pct >= 95, "connection_pool")
            add(
                "queries_fast",
                f"{regression}x" if regression else "not collected",
                "query mean < 1.5x baseline",
                bool(regression is not None and regression < 1.5),
                "slow_queries",
            )
        case C.MEMORY_LEAK:
            add("memory_high", f"{s.memory_pct:.0f}%", "memory >= 85%", s.memory_pct >= 85, "metrics")
            add("oom_kills", str(s.oom_killed), "pods OOMKilled", s.oom_killed, "kubernetes")
        case C.DEPENDENCY_OUTAGE:
            bad = s.unhealthy_dependencies
            add(
                "dependency_unhealthy",
                ", ".join(bad) if bad else ("none" if bad is not None else "not collected"),
                "at least one dependency unhealthy",
                bool(bad),
                "dependencies",
            )
            add("cpu_normal", cpu, "app CPU < 70%", s.cpu_pct < 70, "metrics")
        case C.CONFIG_REGRESSION:
            changes = s.recent_config_changes
            add(
                "recent_config_change",
                ", ".join(c.key for c in changes) if changes else "none",
                "config changed in the last 6h",
                bool(changes),
                "config_history",
            )
        case C.BAD_DEPLOYMENT:
            add(
                "recent_deployment",
                f"#{s.recent_deployment.id}" if s.recent_deployment else "none",
                "deployment in the last 6h",
                s.recent_deployment is not None,
                "deployments",
            )
            add(
                "new_exceptions",
                ", ".join(s.log_keywords) or "none",
                "exception signature in logs",
                "exception" in s.log_keywords,
                "logs",
            )
        case _:
            pass
    return checks


def evidence_digest(evidence: dict[str, EvidenceItem]) -> list[dict[str, Any]]:
    return [
        {"id": e.id, "kind": e.kind.value, "summary": e.summary}
        for e in sorted(evidence.values(), key=lambda e: e.collected_at)
    ]


class RootCauseAgent:
    name = "root_cause_agent"

    def __init__(self, llm: LLMGateway) -> None:
        self.llm = llm

    def generate(
        self,
        incident_id: str,
        alert: Alert,
        signals: IncidentSignals,
        evidence: dict[str, EvidenceItem],
        runbooks: list[DocumentCitation],
        similar: list[DocumentCitation],
        refuted: list[Hypothesis],
    ) -> list[Hypothesis]:
        context = {
            "alert": alert.model_dump(mode="json"),
            "signals": signals.model_dump(mode="json"),
            "evidence": evidence_digest(evidence),
            "knowledge": [{"chunk_id": c.chunk_id, "title": c.title, "snippet": c.snippet[:400]} for c in runbooks[:3]],
            "similar_incidents": [
                {"chunk_id": c.chunk_id, "title": c.title, "snippet": c.snippet[:300]} for c in similar[:2]
            ],
            "refuted_categories": sorted({h.category.value for h in refuted}),
            "refuted_hypotheses": [h.title for h in refuted],
            "allowed_categories": [c.value for c in HypothesisCategory],
        }
        try:
            output = self.llm.structured(
                LLMTask.GENERATE_HYPOTHESES, context, agent=self.name, incident_id=incident_id, schema=HypothesesOutput
            )
            drafts = output.hypotheses
        except LLMError as exc:
            logger.warning("hypothesis generation failed, using fallback: %s", exc)
            return [
                Hypothesis(
                    title="Undetermined cause - human triage required",
                    category=C.UNKNOWN,
                    description=f"Hypothesis generation failed ({exc}).",
                    probability=0.1,
                    rank=1,
                )
            ]
        valid_ids = set(evidence)
        hypotheses = [
            Hypothesis(
                title=d.title,
                category=d.category,
                description=d.description,
                probability=d.probability,
                evidence_for=[e for e in d.evidence_for if e in valid_ids],
                evidence_against=[e for e in d.evidence_against if e in valid_ids],
                required_tests=d.required_tests,
                suspect_deployment_id=d.suspect_deployment_id,
            )
            for d in drafts
        ]
        return self.rank(hypotheses)

    @staticmethod
    def rank(hypotheses: list[Hypothesis]) -> list[Hypothesis]:
        ordered = sorted(hypotheses, key=lambda h: -h.probability)
        for i, h in enumerate(ordered, start=1):
            h.rank = i
        return ordered

    def test(
        self,
        incident_id: str,
        hypothesis: Hypothesis,
        signals: IncidentSignals,
        evidence: dict[str, EvidenceItem],
        iteration: int,
    ) -> HypothesisTestResult:
        missing = missing_evidence(hypothesis.category, signals.collected)
        required = REQUIRED_EVIDENCE.get(hypothesis.category, frozenset())
        coverage = 1.0 if not required else round(1 - len(missing) / len(required), 2)
        checks = signal_checks(hypothesis.category, signals)
        context = {
            "hypothesis": hypothesis.model_dump(
                mode="json", include={"title", "category", "description", "suspect_deployment_id"}
            ),
            "signals": signals.model_dump(mode="json"),
            "evidence": evidence_digest(evidence),
            "missing_evidence": [m.value for m in missing],
        }
        try:
            judgement = self.llm.structured(
                LLMTask.TEST_HYPOTHESIS, context, agent=self.name, incident_id=incident_id, schema=HypothesisJudgement
            )
        except LLMError as exc:
            judgement = HypothesisJudgement(
                verdict="INCONCLUSIVE", confidence=0.0, reasoning=f"judgement unavailable: {exc}", evidence_refs=[]
            )
        verdict = HypothesisStatus(judgement.verdict)
        if missing and verdict == HypothesisStatus.SUPPORTED:
            # Deterministic guard: a hypothesis cannot be "supported" without its required evidence.
            verdict = HypothesisStatus.INCONCLUSIVE
        calibrated = round(min(MAX_CONFIDENCE_WITHOUT_VERIFICATION, judgement.confidence * (0.6 + 0.4 * coverage)), 2)
        return HypothesisTestResult(
            hypothesis_id=hypothesis.id,
            verdict=verdict,
            llm_confidence=judgement.confidence,
            evidence_coverage=coverage,
            calibrated_confidence=calibrated,
            reasoning=judgement.reasoning,
            signals=checks,
            evidence_refs=[r for r in judgement.evidence_refs if r in evidence],
            missing_evidence=missing,
            iteration=iteration,
        )
