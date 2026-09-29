"""Investigation runner: starts, pauses (interrupt) and resumes graph runs with a checkpointer."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from veriops.domain.enums import Environment
from veriops.domain.models import Alert, ApprovalDecision
from veriops.graph.builder import build_graph
from veriops.graph.deps import GraphDeps
from veriops.graph.state import InvestigationState, checkpoint_serializer
from veriops.observability import metrics as obs_metrics

logger = logging.getLogger(__name__)
RECURSION_LIMIT = 150  # hard ceiling on graph steps (loops are also bounded by explicit budgets)


@dataclass
class RunOutcome:
    incident_id: str
    interrupted: bool
    interrupt_payload: dict[str, Any] | None
    state: dict[str, Any]
    next_nodes: tuple[str, ...]


class InvestigationRunner:
    def __init__(self, deps: GraphDeps, checkpointer: BaseCheckpointSaver | None = None) -> None:
        self.deps = deps
        self.checkpointer = checkpointer or InMemorySaver(serde=checkpoint_serializer())
        self.graph = build_graph(deps).compile(checkpointer=self.checkpointer)

    @staticmethod
    def config(incident_id: str) -> RunnableConfig:
        return {"configurable": {"thread_id": incident_id}, "recursion_limit": RECURSION_LIMIT}

    @staticmethod
    def initial_state(incident_id: str, alert: Alert) -> InvestigationState:
        return InvestigationState(
            incident_id=incident_id,
            service_name=alert.service,
            environment=alert.environment or Environment.PRODUCTION.value,
            severity=alert.labels.get("severity", "SEV2"),
            alert_data=alert,
        )

    def _outcome(self, incident_id: str) -> RunOutcome:
        snapshot = self.graph.get_state(self.config(incident_id))
        interrupts = [i for task in snapshot.tasks for i in task.interrupts]
        payload = interrupts[0].value if interrupts else None
        return RunOutcome(
            incident_id=incident_id,
            interrupted=bool(interrupts),
            interrupt_payload=payload,
            state=dict(snapshot.values),
            next_nodes=tuple(snapshot.next),
        )

    def start(self, incident_id: str, alert: Alert) -> RunOutcome:
        started = time.perf_counter()
        self.graph.invoke(self.initial_state(incident_id, alert), self.config(incident_id))
        outcome = self._outcome(incident_id)
        if not outcome.interrupted:
            obs_metrics.INVESTIGATION_DURATION.observe(time.perf_counter() - started)
        return outcome

    def resume(self, incident_id: str, decision: ApprovalDecision) -> RunOutcome:
        self.graph.invoke(Command(resume=decision.model_dump(mode="json")), self.config(incident_id))
        return self._outcome(incident_id)

    def state(self, incident_id: str) -> RunOutcome:
        return self._outcome(incident_id)
