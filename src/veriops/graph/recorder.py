"""Incident recorder: the persistence port used by graph nodes.

Nodes never talk to the database directly. They report facts (timeline events, evidence,
decisions) to an ``IncidentRecorder``. The API wires in ``DbIncidentRecorder``; tests and the
evaluation harness use ``InMemoryRecorder``.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Any, Protocol

from veriops.domain.models import (
    AuditRecord,
    EvidenceItem,
    Hypothesis,
    HypothesisTestResult,
    RemediationPlan,
    TimelineEvent,
    ValidationResult,
)


class IncidentRecorder(Protocol):
    def timeline(self, incident_id: str, event: TimelineEvent) -> None: ...
    def audit(self, record: AuditRecord) -> None: ...
    def evidence(self, incident_id: str, item: EvidenceItem) -> None: ...
    def hypotheses(self, incident_id: str, hypotheses: list[Hypothesis]) -> None: ...
    def hypothesis_test(self, incident_id: str, result: HypothesisTestResult) -> None: ...
    def remediation(self, incident_id: str, plan: RemediationPlan, status: str) -> None: ...
    def validation(self, incident_id: str, result: ValidationResult) -> None: ...
    def approval_requested(self, incident_id: str, plan_id: str, risk: dict[str, Any]) -> None: ...
    def approval_decided(
        self, incident_id: str, plan_id: str, status: str, actor: str, role: str, comment: str
    ) -> None: ...
    def execution(self, incident_id: str, plan_id: str, kind: str, success: bool, payload: dict[str, Any]) -> None: ...
    def metrics_snapshot(self, incident_id: str, phase: str, service: str, metrics: dict[str, Any]) -> None: ...
    def incident_update(self, incident_id: str, **fields: Any) -> None: ...
    def agent_run(
        self, incident_id: str, node: str, agent: str, status: str, duration_ms: float, error: str | None
    ) -> None: ...


class InMemoryRecorder:
    """Thread-safe recorder that keeps everything in memory (tests, evaluations, CLI demo)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.events: dict[str, list[TimelineEvent]] = defaultdict(list)
        self.audits: list[AuditRecord] = []
        self.evidence_items: dict[str, list[EvidenceItem]] = defaultdict(list)
        self.hypothesis_sets: dict[str, list[list[Hypothesis]]] = defaultdict(list)
        self.tests: dict[str, list[HypothesisTestResult]] = defaultdict(list)
        self.plans: dict[str, list[tuple[RemediationPlan, str]]] = defaultdict(list)
        self.validations: dict[str, list[ValidationResult]] = defaultdict(list)
        self.approvals: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.executions: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.snapshots: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.updates: dict[str, dict[str, Any]] = defaultdict(dict)
        self.agent_runs: list[dict[str, Any]] = []

    def timeline(self, incident_id: str, event: TimelineEvent) -> None:
        with self._lock:
            self.events[incident_id].append(event)

    def audit(self, record: AuditRecord) -> None:
        with self._lock:
            self.audits.append(record)

    def evidence(self, incident_id: str, item: EvidenceItem) -> None:
        with self._lock:
            self.evidence_items[incident_id].append(item)

    def hypotheses(self, incident_id: str, hypotheses: list[Hypothesis]) -> None:
        with self._lock:
            self.hypothesis_sets[incident_id].append([h.model_copy() for h in hypotheses])

    def hypothesis_test(self, incident_id: str, result: HypothesisTestResult) -> None:
        with self._lock:
            self.tests[incident_id].append(result)

    def remediation(self, incident_id: str, plan: RemediationPlan, status: str) -> None:
        with self._lock:
            self.plans[incident_id].append((plan, status))

    def validation(self, incident_id: str, result: ValidationResult) -> None:
        with self._lock:
            self.validations[incident_id].append(result)

    def approval_requested(self, incident_id: str, plan_id: str, risk: dict[str, Any]) -> None:
        with self._lock:
            self.approvals[incident_id].append({"plan_id": plan_id, "status": "PENDING", "risk": risk})

    def approval_decided(
        self, incident_id: str, plan_id: str, status: str, actor: str, role: str, comment: str
    ) -> None:
        with self._lock:
            self.approvals[incident_id].append(
                {"plan_id": plan_id, "status": status, "actor": actor, "role": role, "comment": comment}
            )

    def execution(self, incident_id: str, plan_id: str, kind: str, success: bool, payload: dict[str, Any]) -> None:
        with self._lock:
            self.executions[incident_id].append({"plan_id": plan_id, "kind": kind, "success": success, **payload})

    def metrics_snapshot(self, incident_id: str, phase: str, service: str, metrics: dict[str, Any]) -> None:
        with self._lock:
            self.snapshots[incident_id].append({"phase": phase, "service": service, **metrics})

    def incident_update(self, incident_id: str, **fields: Any) -> None:
        with self._lock:
            self.updates[incident_id].update(fields)

    def agent_run(
        self, incident_id: str, node: str, agent: str, status: str, duration_ms: float, error: str | None
    ) -> None:
        with self._lock:
            self.agent_runs.append(
                {
                    "incident_id": incident_id,
                    "node": node,
                    "agent": agent,
                    "status": status,
                    "duration_ms": duration_ms,
                    "error": error,
                }
            )
