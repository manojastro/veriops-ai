"""Remediation nodes: propose → validate (deterministic + sandbox) → risk & policy."""

from __future__ import annotations

from typing import Any

from veriops.domain.enums import ActionType, EvidenceKind, HypothesisStatus, PolicyDecision, ValidationStatus
from veriops.domain.models import RemediationPlan, RiskAssessment, SandboxResult, ValidationResult
from veriops.graph.nodes.base import NodeContext
from veriops.graph.nodes.evidence import collect_evidence
from veriops.graph.nodes.knowledge import search
from veriops.graph.nodes.reasoning import _with_status, latest_test, next_open_hypothesis
from veriops.graph.state import InvestigationState
from veriops.policy.risk import RiskInputs
from veriops.validators.base import ValidationContext

ROLLBACK_AVAILABLE = {
    ActionType.SCALE_DEPLOYMENT: True,
    ActionType.RESTART_DEPLOYMENT: True,
    ActionType.ROLLBACK_DEPLOYMENT: True,
    ActionType.CONFIGURATION_CHANGE: True,
    ActionType.ESCALATE: True,
    ActionType.DATABASE_REMEDIATION: False,
}


def _rejected_for(state: InvestigationState, hypothesis_id: str) -> list[tuple[RemediationPlan, ValidationResult]]:
    validations = {v.plan_id: v for v in state.get("validation_results", [])}
    return [
        (p, validations[p.id])
        for p in state.get("remediation_history", [])
        if p.hypothesis_id == hypothesis_id
        and p.id in validations
        and validations[p.id].status == ValidationStatus.REJECTED
    ]


def generate_remediation_plan(state: InvestigationState, ctx: NodeContext) -> dict[str, Any]:
    hypothesis = state["selected_hypothesis"]
    assert hypothesis is not None
    signals = ctx.signals(state)
    knowledge = search(
        ctx,
        "retrieve_runbook",
        f"{hypothesis.title} {hypothesis.category.value.replace('_', ' ')} remediation procedure",
        state["service_name"],
        top_k=4,
    )
    merged = {c.chunk_id: c for c in knowledge + state.get("retrieved_runbooks", [])}
    citations = sorted(merged.values(), key=lambda c: -c.rerank_score)
    attempt = state.get("remediation_attempts", 0) + 1
    plan = ctx.deps.remediation_agent.propose(
        ctx.incident_id, hypothesis, signals, citations, _rejected_for(state, hypothesis.id), attempt
    )
    ctx.deps.recorder.remediation(ctx.incident_id, plan, "PROPOSED")
    ctx.event(
        "remediation_proposed",
        f"AI proposed remediation: {plan.action.describe()}",
        f"Hypothesis: {hypothesis.title}. Rationale: {plan.rationale}",
        status="info",
        plan_id=plan.id,
        action_type=plan.action.action_type.value,
    )
    ctx.audit(
        "remediation.propose",
        decision=plan.action.action_type.value,
        reason=plan.rationale,
        output=plan.action.model_dump(mode="json"),
    )
    return {
        "proposed_remediation": plan,
        "remediation_history": [plan],
        "remediation_attempts": attempt,
        "remediation_knowledge": citations,
    }


def _sandbox_runner(ctx: NodeContext, service: str):  # type: ignore[no-untyped-def]
    def run(action: dict[str, object]) -> SandboxResult | None:
        result = ctx.deps.tools.invoke(
            "simulate_remediation", {"service": service, "action": action}, ctx.tool_context()
        )
        return SandboxResult(**result.data) if result.ok else None

    return run


def validate_remediation(state: InvestigationState, ctx: NodeContext) -> dict[str, Any]:
    plan = state["proposed_remediation"]
    assert plan is not None
    hypothesis = state.get("selected_hypothesis")
    update: dict[str, Any] = {}

    validator = ctx.deps.validation.validator_for(
        ValidationContext(plan=plan, hypothesis=hypothesis, signals=ctx.signals(state))
    )
    # The validator gathers the evidence its checks need itself (deterministically, read-only).
    if validator is not None:
        missing = [k for k in validator.required_evidence if k not in ctx.by_kind(state)]
        if missing:
            new_evidence, _ = collect_evidence(state, ctx, sorted(missing))
            update["evidence"] = new_evidence
            ctx.event(
                "validation_evidence",
                "Validator collected evidence: " + ", ".join(sorted(m.value for m in missing)),
                "; ".join(i.summary for i in new_evidence.values())[:900],
            )
    merged_state = {**state, "evidence": {**state.get("evidence", {}), **update.get("evidence", {})}}
    signals = ctx.signals(merged_state)  # type: ignore[arg-type]
    by_kind = ctx.by_kind(merged_state)  # type: ignore[arg-type]
    deployments = (
        by_kind[EvidenceKind.DEPLOYMENTS].facts.get("deployments", []) if EvidenceKind.DEPLOYMENTS in by_kind else []
    )
    vctx = ValidationContext(
        plan=plan,
        hypothesis=hypothesis,
        signals=signals,
        deployments=deployments,
        citations=state.get("remediation_knowledge", []) + state.get("retrieved_runbooks", []),
    )
    result = ctx.deps.validation.validate(vctx, _sandbox_runner(ctx, state["service_name"]))
    ctx.deps.recorder.validation(ctx.incident_id, result)
    ctx.deps.recorder.remediation(ctx.incident_id, plan, result.status.value)
    update["validation_results"] = [result]
    ctx.audit(
        "remediation.validate",
        decision=result.status.value,
        reason=result.summary,
        actor=result.validator,
        output={"failed": [c.name for c in result.failed_checks], "passed": [c.name for c in result.passed_checks]},
    )

    if result.status == ValidationStatus.ACCEPTED:
        sandbox = result.sandbox
        detail = ""
        if sandbox:
            detail = (
                f"Sandbox: p95 {sandbox.before['p95_latency_ms']:.0f} → {sandbox.after['p95_latency_ms']:.0f} ms, "
                f"errors {sandbox.before['error_rate_pct']:.1f}% → {sandbox.after['error_rate_pct']:.1f}%; "
                f"{len(result.passed_checks)} checks passed"
            )
        ctx.event(
            "remediation_validated",
            f"REMEDIATION VALIDATED: {plan.action.describe()}",
            detail,
            status="success",
            plan_id=plan.id,
            validation_id=result.id,
        )
        update["next_step"] = "risk"
        return update

    reasons = "; ".join(c.detail for c in result.failed_checks[:5])
    if result.sandbox:
        reasons += (
            f" | Sandbox predicts p95 {result.sandbox.before['p95_latency_ms']:.0f} → "
            f"{result.sandbox.after['p95_latency_ms']:.0f} ms, errors {result.sandbox.before['error_rate_pct']:.1f}% → "
            f"{result.sandbox.after['error_rate_pct']:.1f}%"
        )
    ctx.event(
        "remediation_rejected",
        f"AI RECOMMENDATION REJECTED: {plan.action.describe()}",
        reasons,
        status="danger",
        plan_id=plan.id,
        validation_id=result.id,
        failed_checks=[c.name for c in result.failed_checks],
    )

    hypotheses = state.get("hypotheses", [])
    if result.refutes_hypothesis and hypothesis is not None:
        hypotheses = _with_status(hypotheses, hypothesis.id, HypothesisStatus.REFUTED)
        update["hypotheses"] = hypotheses
        update["selected_hypothesis"] = None
        ctx.event(
            "hypothesis_rejected",
            f"Hypothesis refuted by deterministic validation: {hypothesis.title}",
            reasons,
            status="warning",
            hypothesis_id=hypothesis.id,
        )
        nxt = next_open_hypothesis(hypotheses)
        if nxt is not None:
            update.update(next_step="test_next", current_hypothesis_id=nxt.id)
        elif state.get("hypothesis_generations", 0) < 2:
            update.update(next_step="regenerate", current_hypothesis_id=None)
        else:
            update.update(next_step="escalate", outcome="escalated")
        return update
    budget = ctx.settings.max_remediation_attempts
    attempts_for_hypothesis = sum(
        1 for p in state.get("remediation_history", []) if p.hypothesis_id == plan.hypothesis_id
    )
    if attempts_for_hypothesis < budget and state.get("remediation_attempts", 0) < budget * 3:
        update["next_step"] = "revise"
    else:
        ctx.event("escalation", "Remediation attempts exhausted", status="danger")
        update.update(next_step="escalate", outcome="escalated")
    return update


def calculate_risk(state: InvestigationState, ctx: NodeContext) -> dict[str, Any]:
    plan = state["proposed_remediation"]
    assert plan is not None
    validation = state["validation_results"][-1]
    hypothesis = state.get("selected_hypothesis")
    test = latest_test(state, hypothesis.id) if hypothesis else None
    confidence = test.calibrated_confidence if test else 0.0
    score, level, factors = ctx.deps.risk.assess(
        RiskInputs(
            plan=plan,
            validation=validation,
            environment=state["environment"],
            service_criticality=state.get("service_criticality", "high"),
            confidence=confidence,
            rollback_available=ROLLBACK_AVAILABLE.get(plan.action.action_type, False),
        )
    )
    outcome = ctx.deps.policy.decide(plan, validation, level, state["environment"])
    risk = RiskAssessment(
        score=score,
        level=level,
        factors=factors,
        policy_decision=outcome.decision,
        policy_reasons=outcome.reasons,
        required_approvals=outcome.required_approvals,
    )
    ctx.deps.recorder.incident_update(ctx.incident_id, risk_level=level.value, risk_score=score)
    ctx.event(
        "risk_calculated",
        f"Risk {level.value} ({score:.0f}/100): {outcome.decision.value.replace('_', ' ').lower()}",
        "; ".join(outcome.reasons) + " | " + ", ".join(f"{f.name} {f.contribution:.1f}" for f in factors),
        status={"LOW": "success", "MEDIUM": "warning"}.get(level.value, "danger"),
        score=score,
    )
    ctx.audit(
        "risk.assess",
        decision=outcome.decision.value,
        reason="; ".join(outcome.reasons),
        actor="risk_engine",
        output={"score": score, "level": level.value},
    )
    if plan.action.action_type == ActionType.ESCALATE:
        next_step = "escalate"
    else:
        next_step = {
            PolicyDecision.AUTO_EXECUTE: "execute",
            PolicyDecision.REQUIRE_APPROVAL: "approval",
            PolicyDecision.REQUIRE_APPROVAL_WITH_CHECKS: "approval",
            PolicyDecision.BLOCKED: "blocked",
        }[outcome.decision]
    update: dict[str, Any] = {
        "risk": risk,
        "risk_score": score,
        "policy_decision": outcome.decision,
        "next_step": next_step,
    }
    if next_step in ("escalate", "blocked"):
        update["outcome"] = "escalated"
    return update
