"""Incident routes."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import PlainTextResponse
from sse_starlette.sse import EventSourceResponse

from veriops.agents.report import report_to_markdown
from veriops.api.deps import require, runtime_dep
from veriops.api.incident_service import TERMINAL, IncidentService
from veriops.api.runtime import Runtime
from veriops.api.schemas import CreateIncidentRequest, DecisionRequest
from veriops.domain.enums import ApprovalStatus
from veriops.domain.models import IncidentReport
from veriops.security.auth import Principal

router = APIRouter(prefix="/api/incidents", tags=["incidents"])


def service(rt: Runtime = Depends(runtime_dep)) -> IncidentService:
    return IncidentService(rt)


@router.post("", status_code=201, summary="Create an incident from a simulator scenario or an external alert")
def create_incident(
    body: CreateIncidentRequest,
    svc: IncidentService = Depends(service),
    principal: Principal = Depends(require("incidents:write")),
) -> dict[str, Any]:
    incident = svc.create(principal, body.scenario, body.alert)
    if body.auto_investigate:
        if not principal.can("incidents:investigate"):
            raise HTTPException(403, "auto_investigate requires incidents:investigate")
        incident = svc.investigate(principal, incident["id"])
    return incident


@router.get("", summary="List incidents")
def list_incidents(
    status: str | None = Query(default=None, max_length=32),
    service_name: str | None = Query(default=None, alias="service", max_length=64),
    limit: int = Query(default=50, ge=1, le=200),
    svc: IncidentService = Depends(service),
    _: Principal = Depends(require("incidents:read")),
) -> list[dict[str, Any]]:
    return svc.list_incidents(status, service_name, limit)


@router.get("/{incident_id}", summary="Incident detail including live investigation state")
def get_incident(
    incident_id: str, svc: IncidentService = Depends(service), _: Principal = Depends(require("incidents:read"))
) -> dict[str, Any]:
    return svc.get(incident_id)


@router.post("/{incident_id}/investigate", status_code=202, summary="Start the autonomous investigation")
def investigate(
    incident_id: str,
    svc: IncidentService = Depends(service),
    principal: Principal = Depends(require("incidents:investigate")),
) -> dict[str, Any]:
    return svc.investigate(principal, incident_id)


@router.get("/{incident_id}/timeline", summary="Investigation timeline")
def timeline(
    incident_id: str,
    after_seq: int = Query(default=0, ge=0),
    svc: IncidentService = Depends(service),
    _: Principal = Depends(require("incidents:read")),
) -> list[dict[str, Any]]:
    return svc.timeline(incident_id, after_seq)


@router.get("/{incident_id}/timeline/stream", summary="Server-Sent Events stream of timeline events")
async def timeline_stream(
    incident_id: str,
    request: Request,
    after_seq: int = Query(default=0, ge=0),
    svc: IncidentService = Depends(service),
    _: Principal = Depends(require("incidents:read")),
) -> EventSourceResponse:
    svc.get(incident_id)

    async def events() -> AsyncIterator[dict[str, Any]]:
        last = after_seq
        idle_after_close = 0
        while not await request.is_disconnected():
            rows = await asyncio.to_thread(svc.timeline, incident_id, last)
            for row in rows:
                last = row["seq"]
                yield {"event": "timeline", "id": str(last), "data": json.dumps(jsonable_encoder(row))}
            incident = await asyncio.to_thread(svc._row, incident_id)
            yield {
                "event": "status",
                "data": json.dumps({"status": incident.status, "current_node": incident.current_node}),
            }
            if incident.status in TERMINAL and not rows:
                idle_after_close += 1
                if idle_after_close > 3:
                    yield {"event": "end", "data": "{}"}
                    return
            await asyncio.sleep(0.5)

    return EventSourceResponse(events(), ping=15)


@router.get("/{incident_id}/evidence", summary="Collected evidence, metric snapshots and tool calls")
def evidence(
    incident_id: str, svc: IncidentService = Depends(service), _: Principal = Depends(require("incidents:read"))
) -> dict[str, Any]:
    return svc.evidence(incident_id)


@router.get("/{incident_id}/hypotheses", summary="Hypotheses and their tests")
def hypotheses(
    incident_id: str, svc: IncidentService = Depends(service), _: Principal = Depends(require("incidents:read"))
) -> dict[str, Any]:
    return svc.hypotheses(incident_id)


@router.get("/{incident_id}/remediation", summary="Remediation plans, validations, risk, approvals, executions")
def remediation(
    incident_id: str, svc: IncidentService = Depends(service), _: Principal = Depends(require("incidents:read"))
) -> dict[str, Any]:
    return svc.remediation(incident_id)


@router.post("/{incident_id}/approve", status_code=202, summary="Approve the pending remediation (resumes the graph)")
def approve(
    incident_id: str,
    body: DecisionRequest,
    svc: IncidentService = Depends(service),
    principal: Principal = Depends(require("incidents:approve")),
) -> dict[str, Any]:
    return svc.decide(principal, incident_id, ApprovalStatus.APPROVED, body.comment)


@router.post("/{incident_id}/reject", status_code=202, summary="Reject the pending remediation")
def reject(
    incident_id: str,
    body: DecisionRequest,
    svc: IncidentService = Depends(service),
    principal: Principal = Depends(require("incidents:approve")),
) -> dict[str, Any]:
    return svc.decide(principal, incident_id, ApprovalStatus.REJECTED, body.comment)


@router.post(
    "/{incident_id}/request-more-investigation",
    status_code=202,
    summary="Send the investigation back for more evidence",
)
def more_investigation(
    incident_id: str,
    body: DecisionRequest,
    svc: IncidentService = Depends(service),
    principal: Principal = Depends(require("incidents:approve")),
) -> dict[str, Any]:
    return svc.decide(principal, incident_id, ApprovalStatus.MORE_INVESTIGATION, body.comment)


@router.post(
    "/{incident_id}/execute",
    status_code=202,
    summary="Approve-and-execute the pending remediation (requires incidents:execute)",
)
def execute(
    incident_id: str,
    body: DecisionRequest,
    svc: IncidentService = Depends(service),
    principal: Principal = Depends(require("incidents:execute")),
) -> dict[str, Any]:
    return svc.decide(principal, incident_id, ApprovalStatus.APPROVED, body.comment or "approved via execute")


@router.get("/{incident_id}/audit", summary="Append-only audit log")
def audit(
    incident_id: str,
    limit: int = Query(default=500, ge=1, le=5000),
    svc: IncidentService = Depends(service),
    _: Principal = Depends(require("incidents:read")),
) -> list[dict[str, Any]]:
    return svc.audit(incident_id, limit)


@router.get("/{incident_id}/cost", summary="AI cost and token usage for the incident")
def cost(
    incident_id: str, svc: IncidentService = Depends(service), _: Principal = Depends(require("incidents:read"))
) -> dict[str, Any]:
    return svc.cost(incident_id)


@router.get("/{incident_id}/report", summary="Post-incident report (JSON)")
def report(
    incident_id: str, svc: IncidentService = Depends(service), _: Principal = Depends(require("incidents:read"))
) -> dict[str, Any]:
    data = svc.report(incident_id)
    if data is None:
        raise HTTPException(404, "report not generated yet")
    return data


@router.get("/{incident_id}/report.md", response_class=PlainTextResponse, summary="Post-incident report (Markdown)")
def report_markdown(
    incident_id: str, svc: IncidentService = Depends(service), _: Principal = Depends(require("incidents:read"))
) -> str:
    data = svc.report(incident_id)
    if data is None:
        raise HTTPException(404, "report not generated yet")
    return report_to_markdown(IncidentReport.model_validate(data))
