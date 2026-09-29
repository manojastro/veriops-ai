"""Reporting and closure nodes."""

from __future__ import annotations

from typing import Any

from veriops.agents.report import OUTCOME_STATUS, ReportAgent
from veriops.domain.enums import IncidentStatus
from veriops.graph.nodes.base import NodeContext
from veriops.graph.state import InvestigationState
from veriops.observability import metrics as obs_metrics
from veriops.shared.clock import utcnow


def generate_incident_report(state: InvestigationState, ctx: NodeContext) -> dict[str, Any]:
    events_so_far = state.get("timeline", [])
    report = ReportAgent(ctx.deps.llm).build(state)
    ctx.event(
        "report_generated",
        "Post-incident report generated",
        f"status {report.status}; {len(report.rejected_remediations)} rejected remediation(s); "
        f"{len(report.evidence)} evidence items",
        status="info",
    )
    # include the event we just emitted in the report timeline
    report = report.model_copy(update={"timeline": events_so_far + ctx.events})
    ctx.deps.recorder.incident_update(ctx.incident_id, report=report.model_dump(mode="json"))
    return {"report": report}


def close_incident(state: InvestigationState, ctx: NodeContext) -> dict[str, Any]:
    outcome = "failed" if state.get("fatal_error") else state.get("outcome", "escalated")
    status = IncidentStatus(OUTCOME_STATUS.get(outcome, "ESCALATED"))
    fields: dict[str, Any] = {"status": status.value, "current_node": "close_incident"}
    if status == IncidentStatus.RESOLVED:
        fields["resolved_at"] = utcnow()
    ctx.deps.recorder.incident_update(ctx.incident_id, **fields)
    obs_metrics.INVESTIGATIONS.labels(status.value).inc()
    title = {
        IncidentStatus.RESOLVED: "Incident resolved",
        IncidentStatus.ROLLED_BACK: "Incident closed: remediation rolled back",
        IncidentStatus.REMEDIATION_REJECTED: "Incident closed: remediation rejected by human",
        IncidentStatus.ESCALATED: "Incident escalated to on-call",
        IncidentStatus.FAILED: "Investigation failed",
    }
    ctx.event(
        "incident_closed",
        title.get(status, f"Incident {status.value}"),
        status="success" if status == IncidentStatus.RESOLVED else "warning",
    )
    ctx.audit("incident.close", decision=status.value, actor="system")
    return {"status": status}
