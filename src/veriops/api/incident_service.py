"""Application service for incidents: creation, investigation control, and read models."""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select

from veriops.api.runtime import Runtime
from veriops.api.schemas import AlertInput
from veriops.bootstrap import alert_from_activation
from veriops.db.models import (
    AgentRunRow,
    ApprovalRow,
    AuditEventRow,
    EvidenceRow,
    ExecutionRow,
    HypothesisRow,
    HypothesisTestRow,
    IncidentEventRow,
    IncidentRow,
    LLMCallRow,
    MetricsSnapshotRow,
    RemediationPlanRow,
    ToolCallRow,
    ValidationRunRow,
)
from veriops.db.session import session_scope
from veriops.domain.enums import ActorType, ApprovalStatus, IncidentStatus
from veriops.domain.models import Alert, ApprovalDecision, AuditRecord, TimelineEvent
from veriops.rag.ingest import DEFAULT_KNOWLEDGE_DIR
from veriops.security.auth import Principal
from veriops.shared.clock import utcnow
from veriops.shared.errors import ToolError
from veriops.simulator.scenarios import SCENARIOS
from veriops.tools.backends import HttpSimulatorBackend, InProcessSimulatorBackend

TERMINAL = {
    s.value
    for s in (
        IncidentStatus.RESOLVED,
        IncidentStatus.ROLLED_BACK,
        IncidentStatus.ESCALATED,
        IncidentStatus.REMEDIATION_REJECTED,
        IncidentStatus.FAILED,
    )
}


def new_incident_id() -> str:
    return f"INC-{utcnow():%Y%m%d}-{secrets.token_hex(3).upper()}"


def _row_dict(row: Any, exclude: set[str] | None = None) -> dict[str, Any]:
    exclude = exclude or set()
    return {c.key: getattr(row, c.key) for c in row.__mapper__.column_attrs if c.key not in exclude}


class IncidentService:
    def __init__(self, runtime: Runtime) -> None:
        self.rt = runtime

    # --- commands -------------------------------------------------------------------------------

    def _activate(self, scenario_id: str) -> dict[str, Any]:
        if scenario_id not in SCENARIOS:
            raise HTTPException(404, f"unknown scenario '{scenario_id}'")
        backend = self.rt.platform.backend
        try:
            if isinstance(backend, HttpSimulatorBackend):
                activation = backend.activate_scenario(scenario_id)
            elif isinstance(backend, InProcessSimulatorBackend):
                activation = backend.world.activate(scenario_id)
            else:  # pragma: no cover - real backends do not inject faults
                raise HTTPException(400, "scenario activation requires the simulator backend")
        except ToolError as exc:
            raise HTTPException(503, f"simulator unavailable: {exc}") from exc
        if SCENARIOS[scenario_id].inject_malicious_runbook:
            for path in sorted((DEFAULT_KNOWLEDGE_DIR / "adversarial").glob("*.md")):
                self.rt.platform.ingestor.ingest_file(path)
            self.rt.platform.retriever.invalidate()
        return activation

    def create(self, principal: Principal, scenario: str | None, alert_input: AlertInput | None) -> dict[str, Any]:
        if scenario:
            alert = alert_from_activation(self._activate(scenario))
        else:
            assert alert_input is not None
            alert = Alert(**alert_input.model_dump(), labels={"source": "api"})
        incident_id = new_incident_id()
        with session_scope() as s:
            s.add(
                IncidentRow(
                    id=incident_id,
                    title=alert.title,
                    service=alert.service,
                    environment=alert.environment,
                    severity=alert.labels.get("severity", "SEV2"),
                    status=IncidentStatus.OPEN.value,
                    scenario=scenario,
                    alert=alert.model_dump(mode="json"),
                    created_by=principal.username,
                    thread_id=incident_id,
                    detected_at=utcnow(),
                )
            )
        self.rt.recorder.audit(
            AuditRecord(
                incident_id=incident_id,
                actor=principal.username,
                actor_type=ActorType.HUMAN,
                action="incident.open",
                input={"scenario": scenario, "alert": alert.model_dump(mode="json")},
                decision="OPEN",
            )
        )
        return self.get(incident_id)

    def investigate(self, principal: Principal, incident_id: str) -> dict[str, Any]:
        incident = self._row(incident_id)
        if incident.status != IncidentStatus.OPEN.value:
            raise HTTPException(409, f"incident is {incident.status}; investigation already started")
        self.rt.recorder.incident_update(incident_id, status=IncidentStatus.INVESTIGATING.value)
        self.rt.recorder.timeline(
            incident_id,
            TimelineEvent(
                type="investigation_started", title=f"Investigation started by {principal.username}", status="info"
            ),
        )
        self.rt.recorder.audit(
            AuditRecord(
                incident_id=incident_id,
                actor=principal.username,
                actor_type=ActorType.HUMAN,
                action="incident.investigate",
                decision="START",
            )
        )
        self.rt.submit({"type": "start", "incident_id": incident_id, "alert": incident.alert})
        return self.get(incident_id)

    def decide(self, principal: Principal, incident_id: str, status: ApprovalStatus, comment: str) -> dict[str, Any]:
        incident = self._row(incident_id)
        if incident.status != IncidentStatus.AWAITING_APPROVAL.value:
            raise HTTPException(409, f"incident is {incident.status}; no approval is pending")
        decision = ApprovalDecision(status=status, actor=principal.username, role=principal.role.value, comment=comment)
        self.rt.recorder.incident_update(
            incident_id, status=IncidentStatus.INVESTIGATING.value, approval_status=status.value
        )
        self.rt.submit({"type": "resume", "incident_id": incident_id, "decision": decision.model_dump(mode="json")})
        return self.get(incident_id)

    # --- queries --------------------------------------------------------------------------------

    def _row(self, incident_id: str) -> IncidentRow:
        with session_scope() as s:
            row = s.get(IncidentRow, incident_id)
            if row is None:
                raise HTTPException(404, "incident not found")
            s.expunge(row)
            return row

    def list(self, status: str | None, service: str | None, limit: int) -> list[dict[str, Any]]:
        with session_scope() as s:
            q = select(IncidentRow).order_by(IncidentRow.created_at.desc()).limit(limit)
            if status:
                q = q.where(IncidentRow.status == status)
            if service:
                q = q.where(IncidentRow.service == service)
            return [_row_dict(r, {"report"}) for r in s.scalars(q).all()]

    def graph_state(self, incident_id: str) -> dict[str, Any]:
        try:
            outcome = self.rt.runner.state(incident_id)
        except Exception:
            return {}
        state = outcome.state
        keep = [
            "hypotheses",
            "tested_hypotheses",
            "selected_hypothesis",
            "proposed_remediation",
            "risk",
            "approval",
            "approval_status",
            "execution_result",
            "verification_result",
            "rollback_result",
            "root_cause_confidence",
            "investigation_iterations",
            "remediation_attempts",
            "hypothesis_generations",
            "retrieved_runbooks",
            "similar_incidents",
            "remediation_knowledge",
            "policy_decision",
            "slo",
            "pre_remediation_metrics",
            "service_criticality",
            "outcome",
            "errors",
        ]
        data = {k: state.get(k) for k in keep if k in state}
        data["awaiting_approval"] = outcome.interrupted
        data["approval_request"] = outcome.interrupt_payload
        data["next_nodes"] = list(outcome.next_nodes)
        return jsonable_encoder(data)

    def get(self, incident_id: str) -> dict[str, Any]:
        row = self._row(incident_id)
        with session_scope() as s:
            counts = {
                "events": s.scalar(select(func.count()).where(IncidentEventRow.incident_id == incident_id)),
                "evidence": s.scalar(select(func.count()).where(EvidenceRow.incident_id == incident_id)),
                "tool_calls": s.scalar(select(func.count()).where(ToolCallRow.incident_id == incident_id)),
                "llm_calls": s.scalar(select(func.count()).where(LLMCallRow.incident_id == incident_id)),
                "audit_events": s.scalar(select(func.count()).where(AuditEventRow.incident_id == incident_id)),
                "agent_steps": s.scalar(select(func.count()).where(AgentRunRow.incident_id == incident_id)),
            }
        return {
            **_row_dict(row, {"report"}),
            "has_report": row.report is not None,
            "counts": counts,
            "state": self.graph_state(incident_id),
        }

    def timeline(self, incident_id: str, after_seq: int = 0) -> list[dict[str, Any]]:
        self._row(incident_id)
        with session_scope() as s:
            rows = s.scalars(
                select(IncidentEventRow)
                .where(IncidentEventRow.incident_id == incident_id, IncidentEventRow.seq > after_seq)
                .order_by(IncidentEventRow.seq)
            ).all()
            return [_row_dict(r) for r in rows]

    def evidence(self, incident_id: str) -> dict[str, Any]:
        self._row(incident_id)
        with session_scope() as s:
            items = s.scalars(
                select(EvidenceRow).where(EvidenceRow.incident_id == incident_id).order_by(EvidenceRow.collected_at)
            ).all()
            snapshots = s.scalars(
                select(MetricsSnapshotRow)
                .where(MetricsSnapshotRow.incident_id == incident_id)
                .order_by(MetricsSnapshotRow.captured_at)
            ).all()
            tools = s.scalars(
                select(ToolCallRow).where(ToolCallRow.incident_id == incident_id).order_by(ToolCallRow.created_at)
            ).all()
            return {
                "items": [_row_dict(r) for r in items],
                "metrics_snapshots": [_row_dict(r) for r in snapshots],
                "tool_calls": [_row_dict(r, {"result"}) for r in tools],
            }

    def hypotheses(self, incident_id: str) -> dict[str, Any]:
        self._row(incident_id)
        with session_scope() as s:
            hyps = s.scalars(
                select(HypothesisRow)
                .where(HypothesisRow.incident_id == incident_id)
                .order_by(HypothesisRow.created_at, HypothesisRow.rank)
            ).all()
            tests = s.scalars(
                select(HypothesisTestRow)
                .where(HypothesisTestRow.incident_id == incident_id)
                .order_by(HypothesisTestRow.tested_at)
            ).all()
            return {"hypotheses": [_row_dict(r) for r in hyps], "tests": [r.payload for r in tests]}

    def remediation(self, incident_id: str) -> dict[str, Any]:
        self._row(incident_id)
        with session_scope() as s:
            plans = s.scalars(
                select(RemediationPlanRow)
                .where(RemediationPlanRow.incident_id == incident_id)
                .order_by(RemediationPlanRow.created_at)
            ).all()
            validations = s.scalars(
                select(ValidationRunRow)
                .where(ValidationRunRow.incident_id == incident_id)
                .order_by(ValidationRunRow.validated_at)
            ).all()
            approvals = s.scalars(
                select(ApprovalRow).where(ApprovalRow.incident_id == incident_id).order_by(ApprovalRow.requested_at)
            ).all()
            executions = s.scalars(
                select(ExecutionRow).where(ExecutionRow.incident_id == incident_id).order_by(ExecutionRow.executed_at)
            ).all()
            result = {
                "plans": [_row_dict(r) for r in plans],
                "validations": [r.payload for r in validations],
                "approvals": [_row_dict(r) for r in approvals],
                "executions": [_row_dict(r) for r in executions],
            }
        state = self.graph_state(incident_id)
        result["risk"] = state.get("risk")
        result["approval_request"] = state.get("approval_request")
        result["verification"] = state.get("verification_result")
        return result

    def audit(self, incident_id: str, limit: int = 500) -> list[dict[str, Any]]:
        self._row(incident_id)
        with session_scope() as s:
            rows = s.scalars(
                select(AuditEventRow)
                .where(AuditEventRow.incident_id == incident_id)
                .order_by(AuditEventRow.seq)
                .limit(limit)
            ).all()
            return [_row_dict(r) for r in rows]

    def cost(self, incident_id: str) -> dict[str, Any]:
        self._row(incident_id)
        with session_scope() as s:
            rows = s.scalars(select(LLMCallRow).where(LLMCallRow.incident_id == incident_id)).all()
        by_agent: dict[str, dict[str, float]] = {}
        by_model: dict[str, dict[str, float]] = {}
        for r in rows:
            for bucket, key in ((by_agent, r.agent), (by_model, f"{r.provider}/{r.model}")):
                b = bucket.setdefault(key, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0})
                b["calls"] += 1
                b["prompt_tokens"] += r.prompt_tokens
                b["completion_tokens"] += r.completion_tokens
                b["cost_usd"] = round(b["cost_usd"] + r.cost_usd, 6)
        return {
            "incident_id": incident_id,
            "calls": len(rows),
            "failed_calls": sum(1 for r in rows if not r.success),
            "cached_calls": sum(1 for r in rows if r.cached),
            "prompt_tokens": sum(r.prompt_tokens for r in rows),
            "completion_tokens": sum(r.completion_tokens for r in rows),
            "total_tokens": sum(r.total_tokens for r in rows),
            "total_cost_usd": round(sum(r.cost_usd for r in rows), 6),
            "by_agent": by_agent,
            "by_model": by_model,
            "providers": sorted({r.provider for r in rows}),
            "estimated": any(r.provider == "simulated" for r in rows),
        }

    def report(self, incident_id: str) -> dict[str, Any] | None:
        return self._row(incident_id).report
