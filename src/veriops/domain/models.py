"""Investigation, remediation, and governance domain models.

These models flow through the LangGraph state, are persisted to PostgreSQL, and are returned by the
API. LLM outputs are parsed into the ``*Proposal`` / ``*Draft`` schemas in ``veriops.llm.schemas``
and converted into these models by the agents, so an LLM can never directly construct e.g. a
``ValidationResult`` or ``RiskAssessment``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from veriops.domain.enums import (
    ActionType,
    ActorType,
    ApprovalStatus,
    EvidenceKind,
    HypothesisCategory,
    HypothesisStatus,
    PolicyDecision,
    RiskLevel,
    ValidationStatus,
)
from veriops.shared.clock import utcnow
from veriops.shared.ids import new_id


class Alert(BaseModel):
    service: str
    environment: str
    title: str
    p95_latency_ms: float
    error_rate_pct: float
    cpu_pct: float
    memory_pct: float
    db_pool_utilization_pct: float
    fired_at: datetime = Field(default_factory=utcnow)
    labels: dict[str, str] = Field(default_factory=dict)


class EvidenceItem(BaseModel):
    """A single piece of evidence. Every conclusion references evidence by ``id``."""

    id: str = Field(default_factory=lambda: new_id("ev"))
    kind: EvidenceKind
    source_tool: str | None = None
    summary: str
    facts: dict[str, Any] = Field(default_factory=dict)
    collected_at: datetime = Field(default_factory=utcnow)


class DocumentCitation(BaseModel):
    document_id: str
    chunk_id: str
    title: str
    document_type: str
    snippet: str
    vector_score: float
    keyword_score: float
    fused_score: float
    rerank_score: float
    flagged_injection: bool = False


class Hypothesis(BaseModel):
    id: str = Field(default_factory=lambda: new_id("hyp"))
    title: str
    category: HypothesisCategory
    description: str
    probability: float = Field(ge=0.0, le=1.0)
    evidence_for: list[str] = Field(default_factory=list)
    evidence_against: list[str] = Field(default_factory=list)
    required_tests: list[str] = Field(default_factory=list)
    suspect_deployment_id: str | None = None
    status: HypothesisStatus = HypothesisStatus.PROPOSED
    rank: int = 0


class SignalCheck(BaseModel):
    """A deterministic observation about the evidence (e.g. ``cpu_pct < 70``)."""

    name: str
    observed: str
    expectation: str
    holds: bool
    evidence_ref: str | None = None


class HypothesisTestResult(BaseModel):
    hypothesis_id: str
    verdict: HypothesisStatus
    llm_confidence: float = Field(ge=0.0, le=1.0)
    evidence_coverage: float = Field(ge=0.0, le=1.0)
    calibrated_confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    signals: list[SignalCheck] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    missing_evidence: list[EvidenceKind] = Field(default_factory=list)
    iteration: int = 0
    tested_at: datetime = Field(default_factory=utcnow)


class RemediationAction(BaseModel):
    action_type: ActionType
    service: str
    environment: str
    replicas: int | None = None
    deployment_id: str | None = None
    config_key: str | None = None
    config_value: str | None = None
    sql: str | None = None

    def describe(self) -> str:
        match self.action_type:
            case ActionType.SCALE_DEPLOYMENT:
                return f"Scale {self.service} to {self.replicas} replicas"
            case ActionType.ROLLBACK_DEPLOYMENT:
                return f"Roll back deployment #{self.deployment_id} of {self.service}"
            case ActionType.RESTART_DEPLOYMENT:
                return f"Rolling restart of {self.service}"
            case ActionType.CONFIGURATION_CHANGE:
                return f"Set {self.config_key}={self.config_value} on {self.service}"
            case ActionType.DATABASE_REMEDIATION:
                return f"Database remediation on {self.service}: {self.sql}"
            case ActionType.ESCALATE:
                return f"Escalate {self.service} to on-call"
            case _:
                return f"{self.action_type.value} on {self.service}"


class RemediationPlan(BaseModel):
    id: str = Field(default_factory=lambda: new_id("rem"))
    hypothesis_id: str
    action: RemediationAction
    rationale: str
    expected_impact: str
    rollback_strategy: str
    evidence_refs: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    proposed_by: str = "remediation_agent"
    attempt: int = 1
    created_at: datetime = Field(default_factory=utcnow)


class ValidationCheck(BaseModel):
    name: str
    passed: bool
    detail: str
    observed: dict[str, Any] = Field(default_factory=dict)


class SandboxTest(BaseModel):
    name: str
    passed: bool
    threshold: str
    observed: str


class SandboxResult(BaseModel):
    action: str
    before: dict[str, float]
    after: dict[str, float]
    tests: list[SandboxTest]
    passed: bool


class ValidationResult(BaseModel):
    id: str = Field(default_factory=lambda: new_id("val"))
    plan_id: str
    validator: str
    status: ValidationStatus
    failed_checks: list[ValidationCheck] = Field(default_factory=list)
    passed_checks: list[ValidationCheck] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    risk_hint: RiskLevel = RiskLevel.MEDIUM
    sandbox: SandboxResult | None = None
    refutes_hypothesis: bool = False
    summary: str = ""
    validated_at: datetime = Field(default_factory=utcnow)

    @property
    def coverage(self) -> float:
        total = len(self.failed_checks) + len(self.passed_checks)
        return len(self.passed_checks) / total if total else 0.0


class RiskFactor(BaseModel):
    name: str
    weight: float
    value: float = Field(ge=0.0, le=1.0)
    contribution: float
    explanation: str


class RiskAssessment(BaseModel):
    score: float = Field(ge=0.0, le=100.0)
    level: RiskLevel
    factors: list[RiskFactor]
    policy_decision: PolicyDecision
    policy_reasons: list[str] = Field(default_factory=list)
    required_approvals: int = 0


class ApprovalDecision(BaseModel):
    status: ApprovalStatus
    actor: str
    role: str
    comment: str = ""
    decided_at: datetime = Field(default_factory=utcnow)


class ExecutionResult(BaseModel):
    plan_id: str
    action: str
    success: bool
    detail: str
    previous_state: dict[str, Any] = Field(default_factory=dict)
    executed_at: datetime = Field(default_factory=utcnow)


class VerificationResult(BaseModel):
    passed: bool
    before: dict[str, float]
    after: dict[str, float]
    checks: list[ValidationCheck]
    verified_at: datetime = Field(default_factory=utcnow)


class RollbackResult(BaseModel):
    success: bool
    action: str
    detail: str
    rolled_back_at: datetime = Field(default_factory=utcnow)


class TimelineEvent(BaseModel):
    id: str = Field(default_factory=lambda: new_id("evt"))
    ts: datetime = Field(default_factory=utcnow)
    type: str
    title: str
    detail: str = ""
    node: str | None = None
    status: str = "info"  # info | success | warning | danger
    data: dict[str, Any] = Field(default_factory=dict)


class AuditRecord(BaseModel):
    id: str = Field(default_factory=lambda: new_id("aud"))
    ts: datetime = Field(default_factory=utcnow)
    incident_id: str | None
    actor: str
    actor_type: ActorType
    agent: str | None = None
    tool: str | None = None
    action: str
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    decision: str | None = None
    reason: str | None = None
    approval: str | None = None
    execution: str | None = None


class NodeError(BaseModel):
    node: str
    error_type: str
    message: str
    recoverable: bool
    ts: datetime = Field(default_factory=utcnow)


class IncidentReport(BaseModel):
    incident_id: str
    title: str
    status: str
    summary: str
    impact: str
    detection: str
    timeline: list[TimelineEvent]
    evidence: list[EvidenceItem]
    root_cause: str
    root_cause_evidence: list[str]
    rejected_hypotheses: list[Hypothesis]
    rejected_remediations: list[dict[str, Any]]
    final_remediation: dict[str, Any] | None
    validation: dict[str, Any] | None
    recovery_metrics: dict[str, Any] | None
    lessons_learned: list[str]
    preventive_actions: list[str]
    ai_decisions: list[str]
    human_decisions: list[str]
    generated_at: datetime = Field(default_factory=utcnow)
