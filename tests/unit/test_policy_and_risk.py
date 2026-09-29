"""Risk engine and policy engine tests."""

from __future__ import annotations

import pytest

from veriops.domain.enums import ActionType, PolicyDecision, RiskLevel, ValidationStatus
from veriops.domain.models import RemediationAction, RemediationPlan, ValidationCheck, ValidationResult
from veriops.policy.engine import PolicyEngine
from veriops.policy.risk import WEIGHTS, RiskEngine, RiskInputs, level_for


def _plan(action_type: ActionType, env: str = "production") -> RemediationPlan:
    return RemediationPlan(
        hypothesis_id="h",
        action=RemediationAction(
            action_type=action_type, service="checkout-service", environment=env, deployment_id="842", replicas=6
        ),
        rationale="r",
        expected_impact="i",
        rollback_strategy="s",
    )


def _validation(
    status: ValidationStatus = ValidationStatus.ACCEPTED, passed: int = 10, failed: int = 0
) -> ValidationResult:
    return ValidationResult(
        plan_id="p",
        validator="v",
        status=status,
        passed_checks=[ValidationCheck(name=f"p{i}", passed=True, detail="") for i in range(passed)],
        failed_checks=[ValidationCheck(name=f"f{i}", passed=False, detail="") for i in range(failed)],
    )


def test_weights_sum_to_100() -> None:
    assert sum(WEIGHTS.values()) == 100


@pytest.mark.parametrize(
    ("score", "level"),
    [
        (0, RiskLevel.LOW),
        (30, RiskLevel.LOW),
        (31, RiskLevel.MEDIUM),
        (60, RiskLevel.MEDIUM),
        (61, RiskLevel.HIGH),
        (80, RiskLevel.HIGH),
        (81, RiskLevel.CRITICAL),
        (100, RiskLevel.CRITICAL),
    ],
)
def test_level_bands(score: float, level: RiskLevel) -> None:
    assert level_for(score) == level


def test_flagship_rollback_is_medium_risk() -> None:
    score, level, factors = RiskEngine().assess(
        RiskInputs(
            plan=_plan(ActionType.ROLLBACK_DEPLOYMENT),
            validation=_validation(),
            environment="production",
            service_criticality="critical",
            confidence=0.86,
            rollback_available=True,
        )
    )
    assert level == RiskLevel.MEDIUM
    assert 40 <= score <= 60
    assert {f.name for f in factors} == set(WEIGHTS)


def test_staging_rollback_is_low_risk() -> None:
    _, level, _ = RiskEngine().assess(
        RiskInputs(
            plan=_plan(ActionType.ROLLBACK_DEPLOYMENT, "staging"),
            validation=_validation(),
            environment="staging",
            service_criticality="critical",
            confidence=0.83,
            rollback_available=True,
        )
    )
    assert level == RiskLevel.LOW


def test_database_change_is_high_risk() -> None:
    _, level, _ = RiskEngine().assess(
        RiskInputs(
            plan=_plan(ActionType.DATABASE_REMEDIATION),
            validation=_validation(),
            environment="production",
            service_criticality="critical",
            confidence=0.8,
            rollback_available=False,
        )
    )
    assert level in (RiskLevel.HIGH, RiskLevel.CRITICAL)


def test_destructive_action_scores_100() -> None:
    score, level, _ = RiskEngine().assess(
        RiskInputs(
            plan=_plan(ActionType.DROP_TABLE),
            validation=_validation(),
            environment="production",
            service_criticality="critical",
            confidence=1.0,
            rollback_available=True,
        )
    )
    assert score == 100 and level == RiskLevel.CRITICAL


@pytest.mark.parametrize(
    ("level", "env", "action", "expected"),
    [
        (RiskLevel.LOW, "staging", ActionType.ROLLBACK_DEPLOYMENT, PolicyDecision.AUTO_EXECUTE),
        (RiskLevel.LOW, "production", ActionType.RESTART_DEPLOYMENT, PolicyDecision.AUTO_EXECUTE),
        (RiskLevel.LOW, "production", ActionType.ROLLBACK_DEPLOYMENT, PolicyDecision.REQUIRE_APPROVAL),
        (RiskLevel.MEDIUM, "production", ActionType.ROLLBACK_DEPLOYMENT, PolicyDecision.REQUIRE_APPROVAL),
        (RiskLevel.HIGH, "production", ActionType.DATABASE_REMEDIATION, PolicyDecision.REQUIRE_APPROVAL_WITH_CHECKS),
        (RiskLevel.CRITICAL, "production", ActionType.ROLLBACK_DEPLOYMENT, PolicyDecision.BLOCKED),
    ],
)
def test_policy_matrix(level: RiskLevel, env: str, action: ActionType, expected: PolicyDecision) -> None:
    assert PolicyEngine().decide(_plan(action, env), _validation(), level, env).decision == expected


@pytest.mark.critical
@pytest.mark.parametrize(
    "action",
    [
        ActionType.DROP_TABLE,
        ActionType.DELETE_DATABASE,
        ActionType.DESTROY_INFRASTRUCTURE,
        ActionType.ROTATE_CORE_CREDENTIALS,
        ActionType.DISABLE_SECURITY_CONTROLS,
        ActionType.REMOVE_BACKUPS,
    ],
)
def test_never_autonomous_actions_are_blocked_even_at_low_risk(action: ActionType) -> None:
    outcome = PolicyEngine().decide(_plan(action, "development"), _validation(), RiskLevel.LOW, "development")
    assert outcome.decision == PolicyDecision.BLOCKED


def test_unvalidated_plan_is_blocked() -> None:
    outcome = PolicyEngine().decide(
        _plan(ActionType.RESTART_DEPLOYMENT), _validation(ValidationStatus.REJECTED, 3, 1), RiskLevel.LOW, "production"
    )
    assert outcome.decision == PolicyDecision.BLOCKED
