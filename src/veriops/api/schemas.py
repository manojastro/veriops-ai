"""API request/response schemas (input validation lives here)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

SERVICE_PATTERN = r"^[a-z][a-z0-9-]{1,62}$"


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 - OAuth2 token type, not a secret
    user: dict[str, Any]


class AlertInput(BaseModel):
    service: str = Field(pattern=SERVICE_PATTERN)
    environment: str = Field(default="production", pattern=r"^(production|staging|development)$")
    title: str = Field(min_length=3, max_length=200)
    p95_latency_ms: float = Field(ge=0, le=600_000)
    error_rate_pct: float = Field(ge=0, le=100)
    cpu_pct: float = Field(ge=0, le=100)
    memory_pct: float = Field(ge=0, le=100)
    db_pool_utilization_pct: float = Field(ge=0, le=100)


class CreateIncidentRequest(BaseModel):
    scenario: str | None = Field(default=None, pattern=r"^[a-z0-9_]{3,64}$")
    alert: AlertInput | None = None
    auto_investigate: bool = False

    @model_validator(mode="after")
    def exactly_one_source(self) -> CreateIncidentRequest:
        if (self.scenario is None) == (self.alert is None):
            raise ValueError("provide exactly one of 'scenario' or 'alert'")
        return self


class DecisionRequest(BaseModel):
    comment: str = Field(default="", max_length=2000)


class IncidentSummary(BaseModel):
    id: str
    title: str
    service: str
    environment: str
    severity: str
    status: str
    scenario: str | None
    root_cause: str | None
    risk_level: str | None
    risk_score: float | None
    approval_status: str | None
    current_node: str | None
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    alert: dict[str, Any]


class KnowledgeDocumentRequest(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    content: str = Field(min_length=20, max_length=200_000)
    document_type: str = Field(
        pattern=r"^(runbook|service_doc|architecture|incident_report|deployment_procedure|"
        r"database_guide|rollback_procedure|slo)$"
    )
    service: str | None = Field(default=None, pattern=SERVICE_PATTERN)
    environment: str | None = Field(default=None, max_length=32)
    team: str | None = Field(default=None, max_length=64)
    severity: str | None = Field(default=None, pattern=r"^SEV[1-4]$")
    incident_id: str | None = Field(default=None, max_length=40)
    version: str = Field(default="1", max_length=16)


class EvaluationRequest(BaseModel):
    scenario_ids: list[str] | None = Field(default=None, max_length=50)
