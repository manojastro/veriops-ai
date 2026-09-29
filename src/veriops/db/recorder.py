"""PostgreSQL implementation of the recorder, tool observer and LLM observer ports."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import update

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
    LogEvidenceRow,
    MetricsSnapshotRow,
    RemediationPlanRow,
    ToolCallRow,
    TraceEvidenceRow,
    ValidationRunRow,
)
from veriops.db.session import session_scope
from veriops.domain.enums import ActorType, EvidenceKind
from veriops.domain.models import (
    AuditRecord,
    EvidenceItem,
    Hypothesis,
    HypothesisTestResult,
    RemediationPlan,
    TimelineEvent,
    ValidationResult,
)
from veriops.llm.gateway import LLMCallRecord
from veriops.observability import metrics as obs_metrics
from veriops.shared.clock import utcnow
from veriops.shared.redaction import redact
from veriops.tools.registry import ToolContext, ToolResult

logger = logging.getLogger(__name__)
INCIDENT_FIELDS = {
    "status",
    "current_node",
    "severity",
    "root_cause",
    "root_cause_category",
    "risk_level",
    "risk_score",
    "approval_status",
    "report",
    "resolved_at",
    "thread_id",
}


def _truncate(data: Any, limit: int = 20_000) -> Any:
    """Keep audit/tool payloads bounded; large telemetry is stored in dedicated evidence tables."""
    import json

    text = json.dumps(data, default=str)
    if len(text) <= limit:
        return data
    return {"truncated": True, "preview": text[:limit]}


class DbIncidentRecorder:
    def __init__(self, database_url: str | None = None) -> None:
        self.url = database_url

    # --- IncidentRecorder ------------------------------------------------------------------------

    def timeline(self, incident_id: str, event: TimelineEvent) -> None:
        with session_scope(self.url) as s:
            s.add(
                IncidentEventRow(
                    id=event.id,
                    incident_id=incident_id,
                    ts=event.ts,
                    type=event.type,
                    title=event.title[:255],
                    detail=event.detail,
                    node=event.node,
                    status=event.status,
                    data=redact(event.data),
                )
            )

    def audit(self, record: AuditRecord) -> None:
        with session_scope(self.url) as s:
            s.add(
                AuditEventRow(
                    id=record.id,
                    ts=record.ts,
                    incident_id=record.incident_id,
                    actor=record.actor,
                    actor_type=record.actor_type.value,
                    agent=record.agent,
                    tool=record.tool,
                    action=record.action,
                    input=_truncate(redact(record.input)),
                    output=_truncate(redact(record.output)),
                    decision=record.decision,
                    reason=record.reason,
                    approval=record.approval,
                    execution=record.execution,
                )
            )

    def evidence(self, incident_id: str, item: EvidenceItem) -> None:
        facts = redact(item.facts)
        with session_scope(self.url) as s:
            s.add(
                EvidenceRow(
                    id=item.id,
                    incident_id=incident_id,
                    kind=item.kind.value,
                    source_tool=item.source_tool,
                    summary=item.summary,
                    facts=_truncate(facts, 60_000),
                    collected_at=item.collected_at,
                )
            )
            if item.kind == EvidenceKind.LOGS:
                s.add(
                    LogEvidenceRow(
                        incident_id=incident_id,
                        evidence_id=item.id,
                        query=str(facts.get("query", ""))[:255],
                        total=int(facts.get("total", 0)),
                        counts_by_level=facts.get("counts_by_level", {}),
                        top_patterns=facts.get("top_patterns", []),
                        samples=facts.get("samples", []),
                    )
                )
            elif item.kind == EvidenceKind.TRACES:
                s.add(
                    TraceEvidenceRow(
                        incident_id=incident_id,
                        evidence_id=item.id,
                        trace_id=str(facts.get("trace_id", "")),
                        duration_ms=float(facts.get("duration_ms", 0)),
                        slowest_span=facts.get("slowest_span"),
                        spans=facts.get("spans", []),
                    )
                )

    def hypotheses(self, incident_id: str, hypotheses: list[Hypothesis]) -> None:
        with session_scope(self.url) as s:
            for h in hypotheses:
                s.merge(
                    HypothesisRow(
                        id=h.id,
                        incident_id=incident_id,
                        title=h.title,
                        category=h.category.value,
                        description=h.description,
                        probability=h.probability,
                        status=h.status.value,
                        rank=h.rank,
                        payload=h.model_dump(mode="json"),
                    )
                )

    def hypothesis_test(self, incident_id: str, result: HypothesisTestResult) -> None:
        with session_scope(self.url) as s:
            s.add(
                HypothesisTestRow(
                    incident_id=incident_id,
                    hypothesis_id=result.hypothesis_id,
                    verdict=result.verdict.value,
                    calibrated_confidence=result.calibrated_confidence,
                    payload=result.model_dump(mode="json"),
                )
            )
            status = result.verdict.value if result.verdict.value != "INCONCLUSIVE" else "PROPOSED"
            s.execute(update(HypothesisRow).where(HypothesisRow.id == result.hypothesis_id).values(status=status))

    def remediation(self, incident_id: str, plan: RemediationPlan, status: str) -> None:
        with session_scope(self.url) as s:
            s.merge(
                RemediationPlanRow(
                    id=plan.id,
                    incident_id=incident_id,
                    hypothesis_id=plan.hypothesis_id,
                    action_type=plan.action.action_type.value,
                    description=plan.action.describe(),
                    status=status,
                    attempt=plan.attempt,
                    payload=plan.model_dump(mode="json"),
                )
            )

    def validation(self, incident_id: str, result: ValidationResult) -> None:
        with session_scope(self.url) as s:
            s.add(
                ValidationRunRow(
                    id=result.id,
                    incident_id=incident_id,
                    plan_id=result.plan_id,
                    validator=result.validator,
                    status=result.status.value,
                    payload=result.model_dump(mode="json"),
                )
            )
            if result.refutes_hypothesis:
                plan = s.get(RemediationPlanRow, result.plan_id)
                if plan is not None:
                    s.execute(
                        update(HypothesisRow).where(HypothesisRow.id == plan.hypothesis_id).values(status="REFUTED")
                    )

    def approval_requested(self, incident_id: str, plan_id: str, risk: dict[str, Any]) -> None:
        with session_scope(self.url) as s:
            s.add(ApprovalRow(incident_id=incident_id, plan_id=plan_id, status="PENDING", risk=risk))

    def approval_decided(
        self, incident_id: str, plan_id: str, status: str, actor: str, role: str, comment: str
    ) -> None:
        with session_scope(self.url) as s:
            s.execute(
                update(ApprovalRow)
                .where(
                    ApprovalRow.incident_id == incident_id,
                    ApprovalRow.plan_id == plan_id,
                    ApprovalRow.status == "PENDING",
                )
                .values(status=status, decided_by=actor, decided_role=role, comment=comment, decided_at=utcnow())
            )

    def execution(self, incident_id: str, plan_id: str, kind: str, success: bool, payload: dict[str, Any]) -> None:
        with session_scope(self.url) as s:
            s.add(
                ExecutionRow(
                    incident_id=incident_id,
                    plan_id=plan_id,
                    kind=kind,
                    success=success,
                    payload=_truncate(redact(payload)),
                )
            )

    def metrics_snapshot(self, incident_id: str, phase: str, service: str, metrics: dict[str, Any]) -> None:
        with session_scope(self.url) as s:
            s.add(
                MetricsSnapshotRow(
                    incident_id=incident_id,
                    phase=phase,
                    service=service or metrics.get("service", ""),
                    p95_latency_ms=float(metrics.get("p95_latency_ms", 0)),
                    error_rate_pct=float(metrics.get("error_rate_pct", 0)),
                    cpu_pct=float(metrics.get("cpu_pct", 0)),
                    memory_pct=float(metrics.get("memory_pct", 0)),
                    db_pool_utilization_pct=float(metrics.get("db_pool_utilization_pct", 0)),
                    rps=float(metrics.get("rps", 0)),
                    series=metrics.get("series", []),
                )
            )

    def incident_update(self, incident_id: str, **fields: Any) -> None:
        values = {k: v for k, v in fields.items() if k in INCIDENT_FIELDS}
        if not values:
            return
        with session_scope(self.url) as s:
            s.execute(update(IncidentRow).where(IncidentRow.id == incident_id).values(**values, updated_at=utcnow()))

    def agent_run(
        self, incident_id: str, node: str, agent: str, status: str, duration_ms: float, error: str | None
    ) -> None:
        with session_scope(self.url) as s:
            s.add(
                AgentRunRow(
                    incident_id=incident_id, node=node, agent=agent, status=status, duration_ms=duration_ms, error=error
                )
            )

    # --- ToolObserver / LLMObserver --------------------------------------------------------------

    def on_tool_call(self, result: ToolResult, ctx: ToolContext) -> None:
        obs_metrics.record_tool_call(
            result.tool, result.permission.value, result.ok, result.latency_ms / 1000, result.cached
        )
        with session_scope(self.url) as s:
            s.add(
                ToolCallRow(
                    id=result.call_id,
                    incident_id=ctx.incident_id,
                    tool=result.tool,
                    permission=result.permission.value,
                    arguments=redact(result.arguments),
                    result=_truncate(redact(result.data), 30_000) if result.ok else None,
                    success=result.ok,
                    attempts=result.attempts,
                    latency_ms=result.latency_ms,
                    cached=result.cached,
                    error=result.error,
                )
            )
        self.audit(
            AuditRecord(
                incident_id=ctx.incident_id,
                actor=ctx.actor,
                actor_type=ActorType.TOOL,
                tool=result.tool,
                action=f"tool.{result.tool}",
                input=result.arguments,
                output={"ok": result.ok, "error": result.error, "cached": result.cached, "attempts": result.attempts},
                decision="ALLOWED" if result.ok or "not granted" not in (result.error or "") else "DENIED",
                reason=result.error,
                approval=ctx.approval_id,
                execution=result.call_id,
            )
        )

    def on_llm_call(self, record: LLMCallRecord) -> None:
        with session_scope(self.url) as s:
            s.add(
                LLMCallRow(
                    incident_id=record.incident_id,
                    agent=record.agent,
                    task=record.task,
                    provider=record.provider,
                    model=record.model,
                    prompt_tokens=record.prompt_tokens,
                    completion_tokens=record.completion_tokens,
                    total_tokens=record.total_tokens,
                    cost_usd=record.cost_usd,
                    latency_ms=record.latency_ms,
                    cached=record.cached,
                    success=record.success,
                    error=record.error,
                )
            )


class MetricsOnlyToolObserver:
    """Tool observer for contexts without a database (evaluations)."""

    def on_tool_call(self, result: ToolResult, ctx: ToolContext) -> None:
        obs_metrics.record_tool_call(
            result.tool, result.permission.value, result.ok, result.latency_ms / 1000, result.cached
        )
        _ = ctx
