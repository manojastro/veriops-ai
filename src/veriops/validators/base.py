"""Validator framework.

Validators are plain Python classes with explicit, individually reported checks. They never call an
LLM. A validator *fails closed*: when the evidence a check needs is missing, the check fails.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from veriops.agents.signals import IncidentSignals
from veriops.domain.enums import ActionType, EvidenceKind, RiskLevel
from veriops.domain.models import DocumentCitation, Hypothesis, RemediationPlan, ValidationCheck


@dataclass
class ValidationContext:
    plan: RemediationPlan
    hypothesis: Hypothesis | None
    signals: IncidentSignals
    deployments: list[dict[str, Any]] = field(default_factory=list)
    citations: list[DocumentCitation] = field(default_factory=list)
    config: dict[str, str] = field(default_factory=dict)

    @property
    def active_deployment(self) -> dict[str, Any] | None:
        return next((d for d in self.deployments if d.get("status") == "active"), None)


def check(name: str, passed: bool, detail: str, **observed: Any) -> ValidationCheck:
    return ValidationCheck(name=name, passed=bool(passed), detail=detail, observed=observed)


class RemediationValidator(ABC):
    name: str
    action_types: frozenset[ActionType]
    required_evidence: frozenset[EvidenceKind] = frozenset()
    risk_hint: RiskLevel = RiskLevel.MEDIUM
    # Check names whose failure means the plan's hypothesis is contradicted by evidence.
    refuting_checks: frozenset[str] = frozenset()
    simulate: bool = True

    def supports(self, action: ActionType) -> bool:
        return action in self.action_types

    @abstractmethod
    def checks(self, ctx: ValidationContext) -> list[ValidationCheck]: ...
