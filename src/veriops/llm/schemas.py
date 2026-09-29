"""Structured-output schemas for every LLM task.

LLMs only ever produce these *drafts/proposals*. Agents convert them into domain models, and
deterministic components (validators, risk engine, policy engine) make the binding decisions.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from veriops.domain.enums import ActionType, HypothesisCategory


class LLMTask(StrEnum):
    CLASSIFY_INCIDENT = "classify_incident"
    SUMMARIZE_EVIDENCE = "summarize_evidence"
    GENERATE_HYPOTHESES = "generate_hypotheses"
    TEST_HYPOTHESIS = "test_hypothesis"
    PROPOSE_REMEDIATION = "propose_remediation"
    WRITE_REPORT = "write_report"


class IncidentClassification(BaseModel):
    severity: Literal["SEV1", "SEV2", "SEV3", "SEV4"]
    affected_capability: str = Field(max_length=200)
    customer_impact: str = Field(max_length=400)


class EvidenceSummary(BaseModel):
    summary: str = Field(max_length=1200)
    key_findings: list[str] = Field(default_factory=list, max_length=10)


class HypothesisDraft(BaseModel):
    title: str = Field(max_length=200)
    category: HypothesisCategory
    description: str = Field(max_length=1000)
    probability: float = Field(ge=0.0, le=1.0)
    evidence_for: list[str] = Field(default_factory=list, max_length=10)
    evidence_against: list[str] = Field(default_factory=list, max_length=10)
    required_tests: list[str] = Field(default_factory=list, max_length=8)
    suspect_deployment_id: str | None = None


class HypothesesOutput(BaseModel):
    hypotheses: list[HypothesisDraft] = Field(min_length=1, max_length=6)


class HypothesisJudgement(BaseModel):
    verdict: Literal["SUPPORTED", "REFUTED", "INCONCLUSIVE"]
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(max_length=1500)
    evidence_refs: list[str] = Field(default_factory=list, max_length=12)


class RemediationProposal(BaseModel):
    action_type: ActionType
    replicas: int | None = Field(default=None, ge=1, le=50)
    deployment_id: str | None = Field(default=None, max_length=20)
    config_key: str | None = Field(default=None, max_length=128)
    config_value: str | None = Field(default=None, max_length=256)
    sql: str | None = Field(default=None, max_length=2000)
    rationale: str = Field(max_length=1500)
    expected_impact: str = Field(max_length=600)
    rollback_strategy: str = Field(max_length=600)
    evidence_refs: list[str] = Field(default_factory=list, max_length=12)
    citations: list[str] = Field(default_factory=list, max_length=8)


class ReportNarrative(BaseModel):
    summary: str = Field(max_length=2000)
    impact: str = Field(max_length=1000)
    lessons_learned: list[str] = Field(default_factory=list, max_length=8)
    preventive_actions: list[str] = Field(default_factory=list, max_length=8)


TASK_SCHEMAS: dict[LLMTask, type[BaseModel]] = {
    LLMTask.CLASSIFY_INCIDENT: IncidentClassification,
    LLMTask.SUMMARIZE_EVIDENCE: EvidenceSummary,
    LLMTask.GENERATE_HYPOTHESES: HypothesesOutput,
    LLMTask.TEST_HYPOTHESIS: HypothesisJudgement,
    LLMTask.PROPOSE_REMEDIATION: RemediationProposal,
    LLMTask.WRITE_REPORT: ReportNarrative,
}
