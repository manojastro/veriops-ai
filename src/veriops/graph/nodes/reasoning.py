"""Reasoning nodes: hypothesis generation, ranking, testing and the evidence-sufficiency gate."""

from __future__ import annotations

from typing import Any

from veriops.domain.enums import HypothesisStatus
from veriops.domain.models import Hypothesis, HypothesisTestResult
from veriops.graph.nodes.base import NodeContext
from veriops.graph.state import InvestigationState

SUFFICIENT_CONFIDENCE = 0.6
MAX_HYPOTHESIS_GENERATIONS = 2
OPEN_STATUSES = (HypothesisStatus.PROPOSED, HypothesisStatus.INCONCLUSIVE)


def next_open_hypothesis(hypotheses: list[Hypothesis], exclude: set[str] | None = None) -> Hypothesis | None:
    exclude = exclude or set()
    candidates = [h for h in hypotheses if h.status == HypothesisStatus.PROPOSED and h.id not in exclude]
    return min(candidates, key=lambda h: h.rank, default=None)


def current_hypothesis(state: InvestigationState) -> Hypothesis | None:
    hid = state.get("current_hypothesis_id")
    return next((h for h in state.get("hypotheses", []) if h.id == hid), None)


def latest_test(state: InvestigationState, hypothesis_id: str) -> HypothesisTestResult | None:
    tests = [t for t in state.get("tested_hypotheses", []) if t.hypothesis_id == hypothesis_id]
    return tests[-1] if tests else None


def _with_status(hypotheses: list[Hypothesis], hid: str, status: HypothesisStatus) -> list[Hypothesis]:
    return [h.model_copy(update={"status": status}) if h.id == hid else h for h in hypotheses]


def generate_hypotheses(state: InvestigationState, ctx: NodeContext) -> dict[str, Any]:
    signals = ctx.signals(state)
    previous = state.get("hypotheses", [])
    refuted = [h for h in previous if h.status == HypothesisStatus.REFUTED]
    generated = ctx.deps.root_cause_agent.generate(
        ctx.incident_id,
        state["alert_data"],
        signals,
        state.get("evidence", {}),
        state.get("retrieved_runbooks", []),
        state.get("similar_incidents", []),
        refuted,
    )
    known = {h.category for h in previous}
    fresh = [h for h in generated if h.category not in known]
    generation = state.get("hypothesis_generations", 0) + 1
    hypotheses = previous + fresh
    ctx.deps.recorder.hypotheses(ctx.incident_id, hypotheses)
    ctx.event(
        "hypotheses_generated",
        f"{len(fresh)} hypotheses generated" + (f" (round {generation})" if generation > 1 else ""),
        " | ".join(f"{h.title} ({h.probability:.0%})" for h in fresh),
    )
    ctx.audit(
        "hypotheses.generate",
        decision=f"{len(fresh)} hypotheses",
        output={"hypotheses": [{"title": h.title, "category": h.category.value, "p": h.probability} for h in fresh]},
    )
    return {"hypotheses": hypotheses, "hypothesis_generations": generation}


def rank_hypotheses(state: InvestigationState, ctx: NodeContext) -> dict[str, Any]:
    hypotheses = state.get("hypotheses", [])
    open_ = sorted([h for h in hypotheses if h.status in OPEN_STATUSES], key=lambda h: -h.probability)
    closed = [h for h in hypotheses if h.status not in OPEN_STATUSES]
    ranked = [h.model_copy(update={"rank": i}) for i, h in enumerate(open_, start=1)]
    top = ranked[0] if ranked else None
    if top:
        ctx.event(
            "hypotheses_ranked",
            f"Top hypothesis: {top.title} ({top.probability:.0%})",
            " > ".join(f"{h.title} {h.probability:.0%}" for h in ranked),
        )
    return {"hypotheses": closed + ranked, "current_hypothesis_id": top.id if top else None}


def test_hypothesis(state: InvestigationState, ctx: NodeContext) -> dict[str, Any]:
    hypotheses = state.get("hypotheses", [])
    hypothesis = current_hypothesis(state)
    if hypothesis is None or hypothesis.status == HypothesisStatus.REFUTED:
        hypothesis = next_open_hypothesis(hypotheses)
    if hypothesis is None:
        ctx.event("hypothesis_test", "No open hypothesis to test", status="warning")
        return {"current_hypothesis_id": None}
    signals = ctx.signals(state)
    result = ctx.deps.root_cause_agent.test(
        ctx.incident_id, hypothesis, signals, state.get("evidence", {}), state.get("investigation_iterations", 0)
    )
    ctx.deps.recorder.hypothesis_test(ctx.incident_id, result)
    status = {"SUPPORTED": "success", "REFUTED": "warning", "INCONCLUSIVE": "info"}[result.verdict.value]
    contradicting = [s for s in result.signals if not s.holds]
    detail = result.reasoning
    if contradicting:
        detail += " | deterministic signals not matching this hypothesis: " + ", ".join(
            f"{s.name} ({s.observed}, expected {s.expectation})" for s in contradicting
        )
    ctx.event(
        "hypothesis_test",
        f"Testing '{hypothesis.title}': {result.verdict.value} (confidence {result.calibrated_confidence:.2f})",
        detail,
        status=status,
        hypothesis_id=hypothesis.id,
        missing=[m.value for m in result.missing_evidence],
    )
    ctx.audit(
        "hypothesis.test",
        decision=result.verdict.value,
        reason=result.reasoning,
        input={"hypothesis": hypothesis.title},
        output={
            "llm_confidence": result.llm_confidence,
            "calibrated_confidence": result.calibrated_confidence,
            "evidence_coverage": result.evidence_coverage,
        },
    )
    new_status = result.verdict if result.verdict != HypothesisStatus.INCONCLUSIVE else HypothesisStatus.PROPOSED
    return {
        "tested_hypotheses": [result],
        "current_hypothesis_id": hypothesis.id,
        "hypotheses": _with_status(hypotheses, hypothesis.id, new_status),
    }


def check_evidence_sufficiency(state: InvestigationState, ctx: NodeContext) -> dict[str, Any]:
    """Deterministic gate deciding the next step of the investigation loop."""
    hypotheses = state.get("hypotheses", [])
    iterations = state.get("investigation_iterations", 0)
    budget_left = iterations < ctx.settings.max_investigation_iterations
    hypothesis = current_hypothesis(state)
    if hypothesis is None:
        return _exhausted(state, ctx, hypotheses, budget_left)
    test = latest_test(state, hypothesis.id)
    if test is None:
        return {"next_step": "test_next"}

    if test.verdict == HypothesisStatus.SUPPORTED and test.calibrated_confidence >= SUFFICIENT_CONFIDENCE:
        ctx.event(
            "root_cause_selected",
            f"Evidence sufficient: '{hypothesis.title}' selected",
            f"calibrated confidence {test.calibrated_confidence:.2f}, evidence coverage {test.evidence_coverage:.0%}",
            status="success",
            hypothesis_id=hypothesis.id,
        )
        ctx.deps.recorder.incident_update(
            ctx.incident_id, root_cause=hypothesis.title, root_cause_category=hypothesis.category.value
        )
        return {
            "next_step": "remediate",
            "selected_hypothesis": hypothesis,
            "root_cause_confidence": test.calibrated_confidence,
        }

    if test.missing_evidence and budget_left:
        ctx.event(
            "evidence_insufficient",
            f"Evidence insufficient for '{hypothesis.title}'",
            "collecting: " + ", ".join(m.value for m in test.missing_evidence),
        )
        return {"next_step": "gather", "pending_evidence": test.missing_evidence}

    if test.verdict == HypothesisStatus.SUPPORTED and budget_left:
        ctx.event(
            "evidence_insufficient",
            f"Confidence too low for '{hypothesis.title}'",
            f"{test.calibrated_confidence:.2f} < {SUFFICIENT_CONFIDENCE}; collecting more evidence",
        )
        return {"next_step": "gather", "pending_evidence": []}

    # Refuted, or inconclusive with no budget left: move on.
    status = HypothesisStatus.REFUTED if test.verdict == HypothesisStatus.REFUTED else HypothesisStatus.INCONCLUSIVE
    hypotheses = _with_status(hypotheses, hypothesis.id, status)
    ctx.event(
        "hypothesis_rejected",
        f"Hypothesis {status.value.lower()}: {hypothesis.title}",
        test.reasoning,
        status="warning",
        hypothesis_id=hypothesis.id,
    )
    nxt = next_open_hypothesis(hypotheses)
    if nxt is not None:
        return {"next_step": "test_next", "hypotheses": hypotheses, "current_hypothesis_id": nxt.id}
    return _exhausted({**state, "hypotheses": hypotheses}, ctx, hypotheses, budget_left)  # type: ignore[typeddict-item]


def _exhausted(
    state: InvestigationState, ctx: NodeContext, hypotheses: list[Hypothesis], budget_left: bool
) -> dict[str, Any]:
    if budget_left and state.get("hypothesis_generations", 0) < MAX_HYPOTHESIS_GENERATIONS:
        ctx.event("hypotheses_exhausted", "All hypotheses rejected; generating new hypotheses", status="warning")
        return {"next_step": "regenerate", "hypotheses": hypotheses, "current_hypothesis_id": None}
    ctx.event(
        "escalation",
        "Investigation budget exhausted without a confirmed root cause",
        "Escalating to the on-call engineer with the collected evidence.",
        status="danger",
    )
    return {"next_step": "escalate", "hypotheses": hypotheses, "outcome": "escalated"}
