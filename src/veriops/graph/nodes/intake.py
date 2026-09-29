"""Intake nodes: create the incident and collect initial context."""

from __future__ import annotations

from typing import Any

from veriops.domain.enums import ApprovalStatus, EvidenceKind, IncidentStatus
from veriops.domain.models import EvidenceItem
from veriops.graph.nodes.base import NodeContext
from veriops.graph.state import InvestigationState
from veriops.llm.schemas import IncidentClassification, LLMTask
from veriops.shared.errors import LLMError


def create_incident(state: InvestigationState, ctx: NodeContext) -> dict[str, Any]:
    alert = state["alert_data"]
    ctx.event(
        "incident_created",
        f"Incident created: {alert.title}",
        f"{alert.service} ({alert.environment}) p95 {alert.p95_latency_ms:.0f} ms, errors "
        f"{alert.error_rate_pct:.1f}%, CPU {alert.cpu_pct:.0f}%, memory {alert.memory_pct:.0f}%, "
        f"DB pool {alert.db_pool_utilization_pct:.0f}%",
        status="danger",
    )
    ctx.audit("incident.create", decision="OPEN", input=alert.model_dump(mode="json"), actor="system")
    ctx.deps.recorder.incident_update(ctx.incident_id, status=IncidentStatus.INVESTIGATING.value)
    return {
        "status": IncidentStatus.INVESTIGATING,
        "approval_status": ApprovalStatus.NOT_REQUIRED,
        "investigation_iterations": 0,
        "hypothesis_generations": 0,
        "remediation_attempts": 0,
        "fatal_error": False,
    }


def collect_initial_context(state: InvestigationState, ctx: NodeContext) -> dict[str, Any]:
    alert = state["alert_data"]
    info = ctx.deps.services.get(alert.service)
    slo = {
        "p95_latency_ms": info.slo_p95_latency_ms if info else 500.0,
        "error_rate_pct": info.slo_error_rate_pct if info else 1.0,
    }
    criticality = info.criticality if info else "high"
    alert_item = EvidenceItem(
        kind=EvidenceKind.ALERT,
        source_tool="alertmanager",
        summary=f"{alert.title}: p95 {alert.p95_latency_ms:.0f} ms (SLO {slo['p95_latency_ms']:.0f}), errors "
        f"{alert.error_rate_pct:.1f}% (SLO {slo['error_rate_pct']}%)",
        facts=alert.model_dump(mode="json"),
    )
    evidence = ctx.record_evidence([alert_item])
    update: dict[str, Any] = {
        "slo": slo,
        "service_criticality": criticality,
        "evidence": evidence,
        "pre_remediation_metrics": {
            "p95_latency_ms": alert.p95_latency_ms,
            "error_rate_pct": alert.error_rate_pct,
            "cpu_pct": alert.cpu_pct,
            "memory_pct": alert.memory_pct,
            "db_pool_utilization_pct": alert.db_pool_utilization_pct,
        },
    }
    try:
        signals = ctx.signals({**state, **update})  # type: ignore[typeddict-item]
        classification = ctx.deps.llm.structured(
            LLMTask.CLASSIFY_INCIDENT,
            {
                "signals": signals.model_dump(mode="json"),
                "criticality": criticality,
                "capability": info.team if info else alert.service,
            },
            agent="incident_coordinator",
            incident_id=ctx.incident_id,
            schema=IncidentClassification,
        )
        update["severity"] = classification.severity
        ctx.event(
            "classified",
            f"Classified {classification.severity}: {classification.customer_impact}",
            f"service criticality: {criticality}",
        )
        ctx.deps.recorder.incident_update(ctx.incident_id, severity=classification.severity)
    except LLMError as exc:
        ctx.event("classification_failed", "Severity classification unavailable", str(exc), status="warning")
    ctx.event(
        "context_collected",
        f"Context: {criticality} service owned by {info.team if info else 'unknown'}",
        f"SLO p95 {slo['p95_latency_ms']:.0f} ms, errors {slo['error_rate_pct']}%",
    )
    return update
