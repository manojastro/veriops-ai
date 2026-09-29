"""Strongly typed LangGraph state.

Fields written by parallel branches (evidence fan-out) or appended over time use reducers. All
values are Pydantic models or primitives; the checkpoint serializer only deserialises classes on an
explicit allow-list (see ``checkpoint_serializer``).
"""

from __future__ import annotations

import inspect
import operator
from typing import Annotated, Any, TypedDict

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from pydantic import BaseModel

from veriops.agents import signals as signals_module
from veriops.domain import enums as enums_module
from veriops.domain import models as models_module
from veriops.domain import telemetry as telemetry_module
from veriops.domain.enums import ApprovalStatus, EvidenceKind, IncidentStatus, PolicyDecision
from veriops.domain.models import (
    Alert,
    ApprovalDecision,
    AuditRecord,
    DocumentCitation,
    EvidenceItem,
    ExecutionResult,
    Hypothesis,
    HypothesisTestResult,
    IncidentReport,
    NodeError,
    RemediationPlan,
    RiskAssessment,
    RollbackResult,
    TimelineEvent,
    ValidationResult,
    VerificationResult,
)


def merge_dicts(left: dict[str, Any] | None, right: dict[str, Any] | None) -> dict[str, Any]:
    return {**(left or {}), **(right or {})}


class InvestigationState(TypedDict, total=False):
    # identity & alert
    incident_id: str
    service_name: str
    environment: str
    severity: str
    service_criticality: str
    slo: dict[str, float]
    alert_data: Alert
    status: IncidentStatus

    # evidence (evidence-first: every conclusion references these ids)
    evidence: Annotated[dict[str, EvidenceItem], merge_dicts]
    pre_remediation_metrics: dict[str, float]

    # knowledge
    retrieved_runbooks: list[DocumentCitation]
    similar_incidents: list[DocumentCitation]
    remediation_knowledge: list[DocumentCitation]

    # reasoning
    hypotheses: list[Hypothesis]
    tested_hypotheses: Annotated[list[HypothesisTestResult], operator.add]
    current_hypothesis_id: str | None
    selected_hypothesis: Hypothesis | None
    root_cause_confidence: float
    hypothesis_generations: int
    investigation_iterations: int
    pending_evidence: list[EvidenceKind]

    # remediation
    proposed_remediation: RemediationPlan | None
    remediation_history: Annotated[list[RemediationPlan], operator.add]
    validation_results: Annotated[list[ValidationResult], operator.add]
    remediation_attempts: int
    risk_score: float | None
    risk: RiskAssessment | None
    policy_decision: PolicyDecision | None

    # approval & execution
    approval_status: ApprovalStatus
    approval: ApprovalDecision | None
    execution_result: ExecutionResult | None
    verification_result: VerificationResult | None
    rollback_result: RollbackResult | None

    # output
    report: IncidentReport | None
    outcome: str

    # routing (set by deterministic gate nodes, read by conditional edges)
    next_step: str

    # bookkeeping
    timeline: Annotated[list[TimelineEvent], operator.add]
    audit_events: Annotated[list[AuditRecord], operator.add]
    errors: Annotated[list[NodeError], operator.add]
    fatal_error: bool


def _pydantic_classes(*modules: Any) -> list[type]:
    classes: list[type] = []
    for module in modules:
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if obj.__module__ == module.__name__ and (
                issubclass(obj, BaseModel) or issubclass(obj, enums_module.StrEnum)
            ):
                classes.append(obj)
    return classes


def checkpoint_serializer() -> JsonPlusSerializer:
    """Serializer that only deserialises VeriOps domain classes (no arbitrary code paths)."""
    allowed = _pydantic_classes(models_module, telemetry_module, enums_module, signals_module)
    return JsonPlusSerializer(allowed_msgpack_modules=allowed)
