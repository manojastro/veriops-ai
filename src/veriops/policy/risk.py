"""Deterministic risk engine.

Risk score = Σ weight_i × value_i with values in [0, 1] and weights summing to 100.
The LLM never sets the score; at most its (calibrated) confidence is one bounded input.

| score  | level    |
|--------|----------|
| 0–30   | LOW      |
| 31–60  | MEDIUM   |
| 61–80  | HIGH     |
| 81–100 | CRITICAL |
"""

from __future__ import annotations

from dataclasses import dataclass

from veriops.domain.enums import ActionType, RiskLevel
from veriops.domain.models import RemediationPlan, RiskFactor, ValidationResult

WEIGHTS = {
    "environment": 20.0,
    "action_type": 25.0,
    "blast_radius": 15.0,
    "confidence": 10.0,
    "rollback_availability": 10.0,
    "reversibility": 10.0,
    "validation_coverage": 10.0,
}
ENVIRONMENT_RISK = {"production": 1.0, "staging": 0.4, "development": 0.1}
ACTION_RISK = {
    ActionType.ESCALATE: 0.0,
    ActionType.RESTART_DEPLOYMENT: 0.3,
    ActionType.SCALE_DEPLOYMENT: 0.4,
    ActionType.ROLLBACK_DEPLOYMENT: 0.5,
    ActionType.CONFIGURATION_CHANGE: 0.6,
    ActionType.DATABASE_REMEDIATION: 0.9,
}
REVERSIBILITY_RISK = {
    ActionType.ESCALATE: 0.0,
    ActionType.SCALE_DEPLOYMENT: 0.0,
    ActionType.RESTART_DEPLOYMENT: 0.1,
    ActionType.ROLLBACK_DEPLOYMENT: 0.2,
    ActionType.CONFIGURATION_CHANGE: 0.2,
    ActionType.DATABASE_REMEDIATION: 0.7,
}
CRITICALITY = {"critical": 1.0, "high": 0.7, "medium": 0.4, "low": 0.2}


def level_for(score: float) -> RiskLevel:
    if score <= 30:
        return RiskLevel.LOW
    if score <= 60:
        return RiskLevel.MEDIUM
    if score <= 80:
        return RiskLevel.HIGH
    return RiskLevel.CRITICAL


@dataclass(frozen=True)
class RiskInputs:
    plan: RemediationPlan
    validation: ValidationResult
    environment: str
    service_criticality: str
    confidence: float
    rollback_available: bool


class RiskEngine:
    def assess(self, inputs: RiskInputs) -> tuple[float, RiskLevel, list[RiskFactor]]:
        action = inputs.plan.action.action_type
        env_value = ENVIRONMENT_RISK.get(inputs.environment, 1.0)
        destructive = action not in ACTION_RISK
        values = {
            "environment": (env_value, f"{inputs.environment} environment"),
            "action_type": (1.0 if destructive else ACTION_RISK[action], f"{action.value} base risk"),
            "blast_radius": (
                CRITICALITY.get(inputs.service_criticality, 1.0) * (1.0 if env_value >= 1 else 0.3),
                f"{inputs.service_criticality} service in {inputs.environment}",
            ),
            "confidence": (round(1 - inputs.confidence, 3), f"root-cause confidence {inputs.confidence:.2f}"),
            "rollback_availability": (
                0.0 if inputs.rollback_available else 1.0,
                "rollback strategy available" if inputs.rollback_available else "no rollback path",
            ),
            "reversibility": (1.0 if destructive else REVERSIBILITY_RISK[action], "how easily the change is undone"),
            "validation_coverage": (
                round(1 - inputs.validation.coverage, 3),
                f"{len(inputs.validation.passed_checks)} of "
                f"{len(inputs.validation.passed_checks) + len(inputs.validation.failed_checks)} checks passed",
            ),
        }
        factors = [
            RiskFactor(
                name=name,
                weight=WEIGHTS[name],
                value=min(1.0, max(0.0, value)),
                contribution=round(WEIGHTS[name] * min(1.0, max(0.0, value)), 2),
                explanation=why,
            )
            for name, (value, why) in values.items()
        ]
        score = round(min(100.0, sum(f.contribution for f in factors)), 1)
        if destructive:
            score = 100.0
        return score, level_for(score), factors
