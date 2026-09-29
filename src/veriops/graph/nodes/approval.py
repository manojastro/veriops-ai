"""Human-in-the-loop nodes.

``request_human_approval`` records the request (side effects happen exactly once) and
``await_approval`` pauses the graph with ``interrupt()``. The graph resumes when the API calls
``Command(resume=decision)``; LangGraph re-executes only ``await_approval``.
"""

from __future__ import annotations

from typing import Any

from langgraph.types import interrupt

from veriops.domain.enums import ActorType, ApprovalStatus, IncidentStatus, PolicyDecision, Role
from veriops.domain.models import ApprovalDecision
from veriops.graph.nodes.base import NodeContext
from veriops.graph.state import InvestigationState
from veriops.observability import metrics as obs_metrics

APPROVER_ROLES = {Role.APPROVER.value, Role.ADMINISTRATOR.value}


def approval_payload(state: InvestigationState) -> dict[str, Any]:
    plan = state["proposed_remediation"]
    risk = state["risk"]
    validation = state["validation_results"][-1]
    hypothesis = state.get("selected_hypothesis")
    assert plan is not None and risk is not None
    return {
        "incident_id": state["incident_id"],
        "plan_id": plan.id,
        "proposed_remediation": plan.action.describe(),
        "action": plan.action.model_dump(mode="json"),
        "expected_impact": plan.expected_impact,
        "rollback_strategy": plan.rollback_strategy,
        "root_cause": hypothesis.title if hypothesis else None,
        "evidence": plan.evidence_refs,
        "validation": {
            "status": validation.status.value,
            "passed": len(validation.passed_checks),
            "failed": len(validation.failed_checks),
        },
        "risk": {"score": risk.score, "level": risk.level.value, "policy": risk.policy_decision.value},
        "requires_comment": risk.policy_decision == PolicyDecision.REQUIRE_APPROVAL_WITH_CHECKS,
    }


def request_human_approval(state: InvestigationState, ctx: NodeContext) -> dict[str, Any]:
    payload = approval_payload(state)
    ctx.deps.recorder.approval_requested(ctx.incident_id, payload["plan_id"], payload["risk"])
    ctx.deps.recorder.incident_update(
        ctx.incident_id, status=IncidentStatus.AWAITING_APPROVAL.value, approval_status=ApprovalStatus.PENDING.value
    )
    obs_metrics.AWAITING_APPROVAL.inc()
    ctx.event(
        "approval_requested",
        f"Human approval requested: {payload['proposed_remediation']}",
        f"Risk {payload['risk']['level']} ({payload['risk']['score']:.0f}/100). Rollback strategy: "
        f"{payload['rollback_strategy']}",
        status="warning",
        plan_id=payload["plan_id"],
    )
    ctx.audit("approval.request", decision="PENDING", input=payload)
    return {"approval_status": ApprovalStatus.PENDING, "status": IncidentStatus.AWAITING_APPROVAL}


def await_approval(state: InvestigationState, ctx: NodeContext) -> dict[str, Any]:
    raw = interrupt(approval_payload(state))
    obs_metrics.AWAITING_APPROVAL.dec()
    plan = state["proposed_remediation"]
    risk = state["risk"]
    assert plan is not None and risk is not None
    try:
        decision = ApprovalDecision.model_validate(raw)
    except Exception:
        decision = ApprovalDecision(
            status=ApprovalStatus.REJECTED, actor="system", role="system", comment="malformed approval payload"
        )
    # Defence in depth: the API enforces RBAC, and the graph re-checks it.
    if decision.status == ApprovalStatus.APPROVED and decision.role not in APPROVER_ROLES:
        decision = decision.model_copy(
            update={"status": ApprovalStatus.REJECTED, "comment": f"role {decision.role} may not approve"}
        )
    if (
        decision.status == ApprovalStatus.APPROVED
        and risk.policy_decision == PolicyDecision.REQUIRE_APPROVAL_WITH_CHECKS
        and not decision.comment.strip()
    ):
        decision = decision.model_copy(
            update={"status": ApprovalStatus.REJECTED, "comment": "HIGH risk approvals require a justification"}
        )

    ctx.deps.recorder.approval_decided(
        ctx.incident_id, plan.id, decision.status.value, decision.actor, decision.role, decision.comment
    )
    ctx.deps.recorder.incident_update(
        ctx.incident_id, approval_status=decision.status.value, status=IncidentStatus.INVESTIGATING.value
    )
    ctx.audit(
        f"approval.{decision.status.value.lower()}",
        decision=decision.status.value,
        reason=decision.comment,
        actor=decision.actor,
        actor_type=ActorType.HUMAN if decision.actor != "system" else ActorType.SYSTEM,
        approval=f"{decision.actor}@{decision.decided_at.isoformat()}",
    )
    label = {
        ApprovalStatus.APPROVED: ("approved", "success"),
        ApprovalStatus.REJECTED: ("rejected", "danger"),
        ApprovalStatus.MORE_INVESTIGATION: ("sent back for more investigation", "warning"),
        ApprovalStatus.TIMED_OUT: ("timed out", "danger"),
    }.get(decision.status, ("updated", "info"))
    ctx.event(
        "approval_decided",
        f"{plan.action.describe()} {label[0]} by {decision.actor} ({decision.role})",
        decision.comment,
        status=label[1],
    )

    next_step = {ApprovalStatus.APPROVED: "execute", ApprovalStatus.MORE_INVESTIGATION: "investigate"}.get(
        decision.status, "rejected"
    )
    update: dict[str, Any] = {
        "approval": decision,
        "approval_status": decision.status,
        "next_step": next_step,
        "status": IncidentStatus.INVESTIGATING,
    }
    if next_step == "investigate":
        update["pending_evidence"] = []
    if next_step == "rejected":
        update["outcome"] = "remediation_rejected" if decision.status == ApprovalStatus.REJECTED else "escalated"
    return update
