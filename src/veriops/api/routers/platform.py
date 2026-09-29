"""Platform routes: health, Prometheus metrics, KPIs, simulator catalog, settings."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import func, select

from veriops import __version__
from veriops.api.deps import require, runtime_dep
from veriops.api.runtime import Runtime
from veriops.db.models import (
    AgentRunRow,
    ApprovalRow,
    EvaluationRunRow,
    IncidentEventRow,
    IncidentRow,
    LLMCallRow,
    RemediationPlanRow,
    ValidationRunRow,
)
from veriops.db.session import check_database, session_scope
from veriops.llm.router import ModelRouter
from veriops.observability.metrics import REGISTRY
from veriops.policy.risk import WEIGHTS
from veriops.security.auth import ROLE_PERMISSIONS, Principal
from veriops.simulator.scenarios import SCENARIOS
from veriops.tools.backends import HttpSimulatorBackend

router = APIRouter(tags=["platform"])


@router.get("/health", summary="Liveness")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.get("/health/ready", summary="Readiness (database, simulator)")
def ready(response: Response, rt: Runtime = Depends(runtime_dep)) -> dict[str, Any]:
    checks = {"database": check_database()}
    backend = rt.platform.backend
    if isinstance(backend, HttpSimulatorBackend):
        try:
            backend.list_scenarios()
            checks["simulator"] = True
        except Exception:
            checks["simulator"] = False
    ok = all(checks.values())
    response.status_code = 200 if ok else 503
    return {"status": "ready" if ok else "degraded", "checks": checks, "llm_provider": rt.settings.llm_provider.value}


@router.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)


@router.get("/api/scenarios", tags=["simulator"], summary="Available incident scenarios")
def scenarios(_: Principal = Depends(require("incidents:read"))) -> list[dict[str, Any]]:
    return [
        {
            "id": s.id,
            "title": s.title,
            "service": s.service,
            "environment": s.environment,
            "severity": s.severity,
            "description": s.description,
            "fault": s.fault.value,
        }
        for s in SCENARIOS.values()
    ]


@router.get("/api/services", tags=["simulator"], summary="Service health overview")
def services(
    rt: Runtime = Depends(runtime_dep), _: Principal = Depends(require("incidents:read"))
) -> list[dict[str, Any]]:
    backend = rt.platform.backend
    if isinstance(backend, HttpSimulatorBackend):
        return backend.services_overview()
    return []


def _seconds(a: datetime | None, b: datetime | None) -> float | None:
    return (b - a).total_seconds() if a and b else None


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


@router.get("/api/kpis", summary="Operational and AI KPIs")
def kpis(_: Principal = Depends(require("incidents:read"))) -> dict[str, Any]:
    with session_scope() as s:
        incidents = s.scalars(select(IncidentRow)).all()
        investigated = [i for i in incidents if i.status != "OPEN"]
        rc_events = dict(
            s.execute(
                select(IncidentEventRow.incident_id, func.min(IncidentEventRow.ts))
                .where(IncidentEventRow.type == "root_cause_selected")
                .group_by(IncidentEventRow.incident_id)
            ).all()
        )
        llm_cost = dict(
            s.execute(
                select(LLMCallRow.incident_id, func.sum(LLMCallRow.cost_usd)).group_by(LLMCallRow.incident_id)
            ).all()
        )
        steps = dict(s.execute(select(AgentRunRow.incident_id, func.count()).group_by(AgentRunRow.incident_id)).all())
        approvals = s.execute(select(ApprovalRow.status, func.count()).group_by(ApprovalRow.status)).all()
        validations = s.execute(select(ValidationRunRow.status, func.count()).group_by(ValidationRunRow.status)).all()
        plans = s.scalar(select(func.count()).select_from(RemediationPlanRow)) or 0
        latest_eval = s.scalars(
            select(EvaluationRunRow)
            .where(EvaluationRunRow.status == "COMPLETED")
            .order_by(EvaluationRunRow.started_at.desc())
            .limit(1)
        ).first()
        mttd, mttrc, mttr = [], [], []
        for i in incidents:
            fired = datetime.fromisoformat(i.alert["fired_at"]) if i.alert.get("fired_at") else None
            if (v := _seconds(fired, i.detected_at)) is not None:
                mttd.append(max(0.0, v))
            if (v := _seconds(i.created_at, rc_events.get(i.id))) is not None:
                mttrc.append(v)
            if (v := _seconds(i.created_at, i.resolved_at)) is not None:
                mttr.append(v)
        approval_counts = dict(approvals)
        decided = sum(v for k, v in approval_counts.items() if k != "PENDING")
        validation_counts = dict(validations)
        resolved = [i for i in incidents if i.status == "RESOLVED"]
        executed = [i for i in incidents if i.status in ("RESOLVED", "ROLLED_BACK")]
        total_cost = sum(float(v or 0) for v in llm_cost.values())
        return {
            "incidents_total": len(incidents),
            "incidents_investigated": len(investigated),
            "incidents_resolved": len(resolved),
            "incidents_awaiting_approval": sum(1 for i in incidents if i.status == "AWAITING_APPROVAL"),
            "mttd_seconds": _mean(mttd),
            "mean_time_to_root_cause_seconds": _mean(mttrc),
            "mttr_seconds": _mean(mttr),
            "autonomous_investigation_success_rate": round(len(rc_events) / len(investigated), 3)
            if investigated
            else None,
            "root_cause_accuracy": (latest_eval.metrics or {}).get("root_cause_accuracy") if latest_eval else None,
            "unsafe_action_rejection_rate": (latest_eval.metrics or {}).get("unsafe_recommendation_rejection_rate")
            if latest_eval
            else None,
            "remediations_proposed": plans,
            "remediations_rejected_by_validation": validation_counts.get("REJECTED", 0),
            "approval_rate": round(approval_counts.get("APPROVED", 0) / decided, 3) if decided else None,
            "rollback_rate": round(sum(1 for i in executed if i.status == "ROLLED_BACK") / len(executed), 3)
            if executed
            else None,
            "total_ai_cost_usd": round(total_cost, 6),
            "mean_ai_cost_per_incident_usd": round(total_cost / len(llm_cost), 6) if llm_cost else None,
            "ai_cost_per_resolved_incident_usd": round(total_cost / len(resolved), 6) if resolved else None,
            "mean_investigation_steps": _mean([float(v) for v in steps.values()]),
            "latest_evaluation_id": latest_eval.id if latest_eval else None,
        }


@router.get("/api/cost", summary="AI cost across all incidents (by agent and model)")
def cost_overview(_: Principal = Depends(require("incidents:read"))) -> dict[str, Any]:
    with session_scope() as s:
        by_agent = s.execute(
            select(
                LLMCallRow.agent, func.count(), func.sum(LLMCallRow.total_tokens), func.sum(LLMCallRow.cost_usd)
            ).group_by(LLMCallRow.agent)
        ).all()
        by_model = s.execute(
            select(
                LLMCallRow.provider,
                LLMCallRow.model,
                func.count(),
                func.sum(LLMCallRow.total_tokens),
                func.sum(LLMCallRow.cost_usd),
            ).group_by(LLMCallRow.provider, LLMCallRow.model)
        ).all()
    return {
        "by_agent": [
            {"agent": a, "calls": c, "tokens": int(t or 0), "cost_usd": round(float(v or 0), 6)}
            for a, c, t, v in by_agent
        ],
        "by_model": [
            {"provider": p, "model": m, "calls": c, "tokens": int(t or 0), "cost_usd": round(float(v or 0), 6)}
            for p, m, c, t, v in by_model
        ],
    }


@router.get("/api/settings", summary="Effective platform configuration (no secrets)")
def settings_view(
    rt: Runtime = Depends(runtime_dep), _: Principal = Depends(require("incidents:read"))
) -> dict[str, Any]:
    s = rt.settings
    return {
        "environment": s.app_env,
        "llm_provider": s.llm_provider.value,
        "embedding_provider": s.embedding_provider.value,
        "execution_mode": s.execution_mode.value,
        "model_routing": ModelRouter(s).table(),
        "limits": {
            "max_investigation_iterations": s.max_investigation_iterations,
            "max_remediation_attempts": s.max_remediation_attempts,
            "max_tool_retries": s.max_tool_retries,
            "max_llm_retries": s.max_llm_retries,
            "tool_timeout_seconds": s.tool_timeout_seconds,
            "rate_limit_per_minute": s.rate_limit_per_minute,
        },
        "risk_weights": WEIGHTS,
        "risk_levels": {"LOW": "0-30", "MEDIUM": "31-60", "HIGH": "61-80", "CRITICAL": "81-100"},
        "policy": {
            "read_only": "automatic",
            "LOW": "may run automatically (production HIGH_RISK_WRITE tools still need a human)",
            "MEDIUM": "human approval required",
            "HIGH": "approval + justification + fresh sandbox run",
            "CRITICAL": "blocked",
            "never_autonomous": [
                "drop_table",
                "delete_database",
                "destroy_infrastructure",
                "rotate_core_credentials",
                "disable_security_controls",
                "remove_backups",
            ],
        },
        "roles": {r.value: sorted(p) for r, p in ROLE_PERMISSIONS.items()},
        "tools": rt.platform.tools.descriptors(),
    }
