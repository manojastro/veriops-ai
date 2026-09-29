"""Node infrastructure: per-node context (timeline/audit emission), instrumentation and error capture."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from langgraph.errors import GraphInterrupt

from veriops.agents.signals import IncidentSignals, extract_signals, latest_by_kind
from veriops.domain.enums import ActorType, EvidenceKind
from veriops.domain.models import AuditRecord, EvidenceItem, NodeError, TimelineEvent
from veriops.graph.deps import GraphDeps
from veriops.graph.state import InvestigationState
from veriops.observability import metrics as obs_metrics
from veriops.observability.tracing import span
from veriops.shared.redaction import redact
from veriops.tools.registry import ToolContext

logger = logging.getLogger(__name__)
NodeFn = Callable[[InvestigationState, "NodeContext"], dict[str, Any]]

NODE_AGENTS = {
    "create_incident": "incident_coordinator",
    "collect_initial_context": "incident_coordinator",
    "query_metrics": "evidence_agent",
    "query_logs": "evidence_agent",
    "query_traces": "evidence_agent",
    "inspect_kubernetes": "evidence_agent",
    "inspect_database": "evidence_agent",
    "inspect_recent_deployments": "evidence_agent",
    "inspect_git_changes": "evidence_agent",
    "gather_additional_evidence": "evidence_agent",
    "retrieve_runbooks": "knowledge_agent",
    "retrieve_similar_incidents": "knowledge_agent",
    "generate_hypotheses": "root_cause_agent",
    "rank_hypotheses": "root_cause_agent",
    "test_hypothesis": "root_cause_agent",
    "check_evidence_sufficiency": "incident_coordinator",
    "generate_remediation_plan": "remediation_agent",
    "validate_remediation": "validation_engine",
    "calculate_risk": "risk_engine",
    "request_human_approval": "incident_coordinator",
    "execute_remediation": "execution_layer",
    "verify_recovery": "verification_engine",
    "rollback_remediation": "execution_layer",
    "generate_incident_report": "report_agent",
    "close_incident": "incident_coordinator",
}


class NodeContext:
    def __init__(self, deps: GraphDeps, node: str, incident_id: str) -> None:
        self.deps = deps
        self.node = node
        self.agent = NODE_AGENTS.get(node, node)
        self.incident_id = incident_id
        self.events: list[TimelineEvent] = []
        self.audits: list[AuditRecord] = []

    @property
    def settings(self):  # type: ignore[no-untyped-def]
        return self.deps.settings

    def tool_context(self) -> ToolContext:
        return ToolContext(incident_id=self.incident_id, actor=f"agent:{self.agent}")

    def event(self, type_: str, title: str, detail: str = "", status: str = "info", **data: Any) -> None:
        event = TimelineEvent(type=type_, title=title, detail=detail, node=self.node, status=status, data=data)
        self.events.append(event)
        self.deps.recorder.timeline(self.incident_id, event)

    def audit(
        self,
        action: str,
        *,
        decision: str | None = None,
        reason: str | None = None,
        input: dict[str, Any] | None = None,
        output: dict[str, Any] | None = None,
        actor: str | None = None,
        actor_type: ActorType = ActorType.AGENT,
        tool: str | None = None,
        approval: str | None = None,
        execution: str | None = None,
    ) -> None:
        record = AuditRecord(
            incident_id=self.incident_id,
            actor=actor or self.agent,
            actor_type=actor_type,
            agent=self.agent,
            tool=tool,
            action=action,
            input=redact(input or {}),
            output=redact(output or {}),
            decision=decision,
            reason=reason,
            approval=approval,
            execution=execution,
        )
        self.audits.append(record)
        self.deps.recorder.audit(record)

    def record_evidence(self, items: list[EvidenceItem]) -> dict[str, EvidenceItem]:
        for item in items:
            self.deps.recorder.evidence(self.incident_id, item)
            if item.kind == EvidenceKind.METRICS:
                self.deps.recorder.metrics_snapshot(
                    self.incident_id, "investigation", item.facts.get("service", ""), item.facts
                )
        return {i.id: i for i in items}

    def signals(self, state: InvestigationState, extra: dict[str, EvidenceItem] | None = None) -> IncidentSignals:
        evidence = {**state.get("evidence", {}), **(extra or {})}
        return extract_signals(state["alert_data"], evidence, state["slo"])

    @staticmethod
    def by_kind(state: InvestigationState) -> dict[EvidenceKind, EvidenceItem]:
        return latest_by_kind(state.get("evidence", {}))


def instrumented(node: str, fn: NodeFn, deps: GraphDeps) -> Callable[[InvestigationState], dict[str, Any]]:
    def run(state: InvestigationState) -> dict[str, Any]:
        incident_id = state["incident_id"]
        ctx = NodeContext(deps, node, incident_id)
        started = time.perf_counter()
        status, error = "success", None
        deps.recorder.incident_update(incident_id, current_node=node)
        try:
            with span(f"graph.node.{node}", {"veriops.incident_id": incident_id, "veriops.agent": ctx.agent}):
                update = fn(state, ctx) or {}
        except GraphInterrupt:
            status = "interrupted"
            raise
        except Exception as exc:  # recoverable at graph level: record and route to report
            logger.exception("node %s failed", node)
            status, error = "error", f"{type(exc).__name__}: {exc}"
            ctx.event("node_error", f"{node} failed", error, status="danger")
            update = {
                "errors": [NodeError(node=node, error_type=type(exc).__name__, message=str(exc), recoverable=False)],
                "fatal_error": True,
            }
        finally:
            duration = (time.perf_counter() - started) * 1000
            obs_metrics.NODE_DURATION.labels(node, status).observe(duration / 1000)
            deps.recorder.agent_run(incident_id, node, ctx.agent, status, duration, error)
        if ctx.events:
            update["timeline"] = update.get("timeline", []) + ctx.events
        if ctx.audits:
            update["audit_events"] = update.get("audit_events", []) + ctx.audits
        return update

    run.__name__ = node
    return run
