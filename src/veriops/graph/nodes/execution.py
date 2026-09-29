"""Execution layer: execute → verify → (automatic) rollback. All writes go through the tool registry
with explicitly granted permissions and the recorded approval."""

from __future__ import annotations

from typing import Any

from veriops.domain.enums import (
    ActionType,
    ActorType,
    ApprovalStatus,
    IncidentStatus,
    PolicyDecision,
    ToolPermission,
)
from veriops.domain.models import (
    ExecutionResult,
    RemediationPlan,
    RollbackResult,
    SandboxResult,
    ValidationCheck,
    VerificationResult,
)
from veriops.graph.nodes.base import NodeContext
from veriops.graph.state import InvestigationState
from veriops.observability import metrics as obs_metrics
from veriops.policy.engine import ACTION_TOOL_PERMISSION
from veriops.tools.catalog import ACTION_TOOL
from veriops.tools.registry import ToolContext


def _write_context(state: InvestigationState, ctx: NodeContext, plan: RemediationPlan) -> ToolContext:
    permission = ACTION_TOOL_PERMISSION[plan.action.action_type]
    approval = state.get("approval")
    if approval is not None and approval.status == ApprovalStatus.APPROVED:
        approval_id = f"human:{approval.actor}@{approval.decided_at.isoformat()}"
    elif state.get("policy_decision") == PolicyDecision.AUTO_EXECUTE:
        approval_id = f"policy:auto:{plan.id}"
    else:
        approval_id = None
    return ToolContext(
        incident_id=ctx.incident_id,
        actor=f"agent:{ctx.agent}",
        granted=frozenset({ToolPermission.READ_ONLY, permission}),
        approval_id=approval_id,
    )


def _tool_args(plan: RemediationPlan) -> dict[str, Any]:
    a = plan.action
    match a.action_type:
        case ActionType.SCALE_DEPLOYMENT:
            return {"service": a.service, "replicas": a.replicas}
        case ActionType.ROLLBACK_DEPLOYMENT:
            return {"service": a.service, "deployment_id": a.deployment_id}
        case ActionType.CONFIGURATION_CHANGE:
            return {"service": a.service, "key": a.config_key, "value": a.config_value}
        case ActionType.DATABASE_REMEDIATION:
            return {"service": a.service, "sql": a.sql}
        case _:
            return {"service": a.service}


def execute_remediation(state: InvestigationState, ctx: NodeContext) -> dict[str, Any]:
    plan = state["proposed_remediation"]
    assert plan is not None
    tool_ctx = _write_context(state, ctx, plan)
    ctx.deps.recorder.incident_update(ctx.incident_id, status=IncidentStatus.REMEDIATING.value)

    if state.get("policy_decision") == PolicyDecision.REQUIRE_APPROVAL_WITH_CHECKS:
        fresh = ctx.deps.tools.invoke(
            "simulate_remediation",
            {"service": plan.action.service, "action": plan.action.model_dump(mode="json")},
            ctx.tool_context(),
        )
        if not fresh.ok or not SandboxResult(**fresh.data).passed:
            result = ExecutionResult(
                plan_id=plan.id,
                action=plan.action.describe(),
                success=False,
                detail="pre-execution sandbox re-check failed; execution aborted",
            )
            ctx.event("execution_aborted", "Execution aborted: pre-execution sandbox re-check failed", status="danger")
            return {"execution_result": result, "next_step": "failed", "outcome": "escalated"}

    previous: dict[str, Any] = {"replicas": ctx.signals(state).replicas}
    tool = ACTION_TOOL[plan.action.action_type.value]
    tool_result = ctx.deps.tools.invoke(tool, _tool_args(plan), tool_ctx)
    if tool_result.ok and isinstance(tool_result.data, dict):
        previous.update(
            {
                k: v
                for k, v in tool_result.data.items()
                if k in ("previous_replicas", "old_value", "rolled_back", "active_deployment")
            }
        )
    execution = ExecutionResult(
        plan_id=plan.id,
        action=plan.action.describe(),
        success=tool_result.ok,
        detail=str(tool_result.data if tool_result.ok else tool_result.error),
        previous_state=previous,
    )
    ctx.deps.recorder.execution(
        ctx.incident_id,
        plan.id,
        "remediation",
        tool_result.ok,
        {"tool": tool, "tool_result": tool_result.data, "error": tool_result.error},
    )
    ctx.audit(
        "remediation.execute",
        decision="SUCCESS" if tool_result.ok else "FAILED",
        tool=tool,
        input=_tool_args(plan),
        output={"tool_result": tool_result.data, "error": tool_result.error},
        approval=tool_ctx.approval_id,
        execution=tool_result.call_id,
        actor_type=ActorType.AGENT,
    )
    obs_metrics.REMEDIATIONS.labels(plan.action.action_type.value, "executed" if tool_result.ok else "failed").inc()
    if not tool_result.ok:
        ctx.event(
            "execution_failed", f"Execution failed: {plan.action.describe()}", tool_result.error or "", status="danger"
        )
        return {"execution_result": execution, "next_step": "failed", "outcome": "escalated"}
    ctx.event(
        "remediation_executed",
        f"Executed: {plan.action.describe()}",
        execution.detail,
        status="success",
        approval=tool_ctx.approval_id,
    )
    return {"execution_result": execution, "status": IncidentStatus.VERIFYING, "next_step": "verify"}


def verify_recovery(state: InvestigationState, ctx: NodeContext) -> dict[str, Any]:
    ctx.deps.recorder.incident_update(ctx.incident_id, status=IncidentStatus.VERIFYING.value)
    service = state["service_name"]
    slo = state["slo"]
    before = state.get("pre_remediation_metrics", {})
    health = ctx.deps.tools.invoke("run_health_check", {"service": service}, ctx.tool_context())
    metrics = ctx.deps.tools.invoke(
        "get_service_metrics", {"service": service, "window_minutes": 15}, ctx.tool_context()
    )
    if not (health.ok and metrics.ok):
        checks = [
            ValidationCheck(
                name="telemetry_available",
                passed=False,
                detail=f"verification telemetry unavailable: {health.error or metrics.error}",
            )
        ]
        after: dict[str, float] = {}
    else:
        m = metrics.data
        after = {
            k: m[k] for k in ("p95_latency_ms", "error_rate_pct", "cpu_pct", "memory_pct", "db_pool_utilization_pct")
        }
        ctx.deps.recorder.metrics_snapshot(ctx.incident_id, "post_remediation", service, m)
        checks = [
            ValidationCheck(
                name="latency_within_slo",
                passed=after["p95_latency_ms"] <= slo["p95_latency_ms"],
                detail=f"p95 {after['p95_latency_ms']:.0f} ms (SLO {slo['p95_latency_ms']:.0f} ms)",
            ),
            ValidationCheck(
                name="error_rate_within_slo",
                passed=after["error_rate_pct"] <= slo["error_rate_pct"],
                detail=f"error rate {after['error_rate_pct']:.2f}% (SLO {slo['error_rate_pct']}%)",
            ),
            ValidationCheck(
                name="db_pool_healthy",
                passed=after["db_pool_utilization_pct"] <= 85,
                detail=f"DB pool {after['db_pool_utilization_pct']:.0f}%",
            ),
            ValidationCheck(
                name="health_endpoint",
                passed=bool(health.data.get("healthy")),
                detail=f"health checks: {health.data.get('checks')}",
            ),
        ]
        if before.get("p95_latency_ms"):
            improved = after["p95_latency_ms"] <= before["p95_latency_ms"] * 0.5 or checks[0].passed
            checks.append(
                ValidationCheck(
                    name="improved_vs_baseline",
                    passed=improved,
                    detail=f"p95 {before['p95_latency_ms']:.0f} → {after['p95_latency_ms']:.0f} ms",
                )
            )
    passed = all(c.passed for c in checks)
    verification = VerificationResult(passed=passed, before=before, after=after, checks=checks)
    plan = state.get("proposed_remediation")
    ctx.deps.recorder.execution(
        ctx.incident_id,
        plan.id if plan else "",
        "verification",
        passed,
        verification.model_dump(mode="json"),
    )
    ctx.audit(
        "remediation.verify",
        decision="PASS" if passed else "FAIL",
        actor="verification_engine",
        output={"checks": {c.name: c.passed for c in checks}},
    )
    if passed:
        ctx.event(
            "service_recovered",
            f"Service recovered: p95 {before.get('p95_latency_ms', 0):.0f} → "
            f"{after['p95_latency_ms']:.0f} ms, errors {before.get('error_rate_pct', 0):.1f}% → "
            f"{after['error_rate_pct']:.1f}%",
            "; ".join(c.detail for c in checks),
            status="success",
        )
        return {"verification_result": verification, "next_step": "resolved", "outcome": "resolved"}
    ctx.event(
        "verification_failed",
        "Recovery verification FAILED — rolling back the remediation",
        "; ".join(c.detail for c in checks if not c.passed),
        status="danger",
    )
    return {"verification_result": verification, "next_step": "rollback"}


def rollback_remediation(state: InvestigationState, ctx: NodeContext) -> dict[str, Any]:
    plan = state["proposed_remediation"]
    execution = state.get("execution_result")
    assert plan is not None
    previous = execution.previous_state if execution else {}
    tool_ctx = _write_context(state, ctx, plan)
    a = plan.action
    match a.action_type:
        case ActionType.SCALE_DEPLOYMENT:
            tool, args = (
                "scale_deployment",
                {"service": a.service, "replicas": previous.get("previous_replicas") or previous.get("replicas")},
            )
        case ActionType.ROLLBACK_DEPLOYMENT:
            tool, args = "restore_deployment_revision", {"service": a.service, "deployment_id": a.deployment_id}
            tool_ctx = ToolContext(
                tool_ctx.incident_id,
                tool_ctx.actor,
                frozenset({ToolPermission.READ_ONLY, ToolPermission.HIGH_RISK_WRITE}),
                tool_ctx.approval_id,
            )
        case ActionType.CONFIGURATION_CHANGE:
            tool, args = (
                "apply_config_change",
                {"service": a.service, "key": a.config_key, "value": previous.get("old_value", "")},
            )
        case _:
            tool, args = "", {}
    if not tool:
        rollback = RollbackResult(
            success=False, action="none", detail=f"{a.action_type.value} has no automatic undo; escalating to on-call"
        )
    else:
        result = ctx.deps.tools.invoke(tool, args, tool_ctx)
        rollback = RollbackResult(
            success=result.ok, action=f"{tool}({args})", detail=str(result.data if result.ok else result.error)
        )
        ctx.audit(
            "remediation.rollback",
            decision="SUCCESS" if result.ok else "FAILED",
            tool=tool,
            input=args,
            output={"result": result.data, "error": result.error},
            approval=tool_ctx.approval_id,
        )
    ctx.deps.recorder.execution(
        ctx.incident_id, plan.id, "rollback", rollback.success, rollback.model_dump(mode="json")
    )
    obs_metrics.REMEDIATIONS.labels(a.action_type.value, "rolled_back").inc()
    ctx.event(
        "remediation_rolled_back",
        f"Remediation rolled back automatically: {rollback.action}",
        rollback.detail,
        status="warning" if rollback.success else "danger",
    )
    return {"rollback_result": rollback, "outcome": "rolled_back"}
