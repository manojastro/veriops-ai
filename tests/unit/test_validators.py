"""Deterministic validator tests, including the safety-critical unsafe-scaling rejection."""

from __future__ import annotations

import pytest

from veriops.agents.signals import ConfigChangeSignal, IncidentSignals, RecentDeployment
from veriops.domain.enums import ActionType, HypothesisCategory, ValidationStatus
from veriops.domain.models import (
    DocumentCitation,
    Hypothesis,
    RemediationAction,
    RemediationPlan,
    SandboxResult,
    SandboxTest,
)
from veriops.validators.base import ValidationContext
from veriops.validators.engine import ValidationEngine

DEPLOYMENTS = [
    {"id": "842", "status": "active", "commit_sha": "a1b2c3d"},
    {"id": "841", "status": "superseded", "commit_sha": "c4d81e0"},
    {"id": "840", "status": "superseded", "commit_sha": "9f1c2ab"},
]


def flagship_signals(**overrides: object) -> IncidentSignals:
    base: dict[str, object] = dict(
        service="checkout-service",
        environment="production",
        slo_p95_latency_ms=500,
        slo_error_rate_pct=1.0,
        p95_latency_ms=4200,
        error_rate_pct=11.8,
        cpu_pct=37,
        memory_pct=52,
        db_pool_utilization_pct=100,
        rps=410,
        replicas=3,
        hpa_max=12,
        latency_breach=True,
        error_breach=True,
        onset_minutes_ago=17,
        recent_deployment=RecentDeployment(
            id="842",
            minutes_ago=18,
            commit_sha="a1b2c3d",
            description="perf: simplify order lookup",
            author="dev.kumar",
        ),
        deployment_precedes_onset=True,
        db_active_connections=124,
        db_max_connections=250,
        pool_per_pod=33.33,
        slow_query_max_regression=13.5,
        slow_query_after_deployment=True,
        log_keywords=["pool_timeout", "slow_query"],
        evidence_refs={"metrics": "ev-m", "connection_pool": "ev-p"},
    )
    base.update(overrides)
    return IncidentSignals(**base)  # type: ignore[arg-type]


def plan_for(action: RemediationAction, hypothesis: Hypothesis) -> RemediationPlan:
    return RemediationPlan(
        hypothesis_id=hypothesis.id, action=action, rationale="test", expected_impact="test", rollback_strategy="test"
    )


CAPACITY = Hypothesis(
    title="checkout-service is under-provisioned",
    category=HypothesisCategory.CAPACITY,
    description="naive",
    probability=0.46,
)
DB_REGRESSION = Hypothesis(
    title="DB query regression after #842",
    category=HypothesisCategory.DB_QUERY_REGRESSION,
    description="query",
    probability=0.34,
    suspect_deployment_id="842",
)


def healthy_sandbox(passed: bool = True) -> SandboxResult:
    return SandboxResult(
        action="x",
        before={"p95_latency_ms": 4200},
        after={"p95_latency_ms": 310},
        tests=[SandboxTest(name="latency_threshold", passed=passed, threshold="<=500", observed="310")],
        passed=passed,
    )


@pytest.mark.critical
def test_scaling_is_rejected_when_db_pool_is_exhausted() -> None:
    """The AI's 'scale checkout 3 -> 8' recommendation MUST be rejected. CI fails if it is ever accepted."""
    action = RemediationAction(
        action_type=ActionType.SCALE_DEPLOYMENT, service="checkout-service", environment="production", replicas=8
    )
    ctx = ValidationContext(
        plan=plan_for(action, CAPACITY), hypothesis=CAPACITY, signals=flagship_signals(), deployments=DEPLOYMENTS
    )
    # Even a (hypothetically) passing sandbox must not override the deterministic checks.
    result = ValidationEngine().validate(ctx, sandbox=lambda _: healthy_sandbox(True))

    assert result.status == ValidationStatus.REJECTED
    failed = {c.name for c in result.failed_checks}
    assert {"cpu_justifies_scaling", "db_pool_headroom", "db_connection_capacity", "no_query_regression"} <= failed
    assert result.refutes_hypothesis, "rejection must refute the capacity hypothesis so the graph re-plans"
    details = " ".join(c.detail for c in result.failed_checks)
    assert "CPU utilization 37%" in details
    assert "increase database connection pressure" in details
    assert "deployment #842" in details


@pytest.mark.critical
def test_scaling_rejected_without_sandbox_and_with_missing_evidence_fails_closed() -> None:
    action = RemediationAction(
        action_type=ActionType.SCALE_DEPLOYMENT, service="checkout-service", environment="production", replicas=8
    )
    signals = flagship_signals(
        db_active_connections=None, slow_query_max_regression=None, cpu_pct=90, db_pool_utilization_pct=40
    )
    result = ValidationEngine().validate(
        ValidationContext(
            plan=plan_for(action, CAPACITY), hypothesis=CAPACITY, signals=signals, deployments=DEPLOYMENTS
        )
    )
    assert result.status == ValidationStatus.REJECTED
    assert {"db_connection_capacity", "no_query_regression"} <= {c.name for c in result.failed_checks}


def test_scaling_accepted_when_cpu_bound_with_db_headroom() -> None:
    action = RemediationAction(
        action_type=ActionType.SCALE_DEPLOYMENT, service="user-service", environment="production", replicas=6
    )
    hyp = Hypothesis(
        title="CPU saturation", category=HypothesisCategory.CPU_SATURATION, description="d", probability=0.6
    )
    signals = flagship_signals(
        service="user-service",
        cpu_pct=96,
        db_pool_utilization_pct=45,
        recent_deployment=None,
        deployment_precedes_onset=False,
        slow_query_max_regression=1.0,
        slow_query_after_deployment=None,
        db_active_connections=69,
    )
    result = ValidationEngine().validate(
        ValidationContext(plan=plan_for(action, hyp), hypothesis=hyp, signals=signals, deployments=DEPLOYMENTS),
        sandbox=lambda _: healthy_sandbox(True),
    )
    assert result.status == ValidationStatus.ACCEPTED, result.summary


def test_rollback_of_regressing_deployment_is_accepted() -> None:
    action = RemediationAction(
        action_type=ActionType.ROLLBACK_DEPLOYMENT,
        service="checkout-service",
        environment="production",
        deployment_id="842",
    )
    result = ValidationEngine().validate(
        ValidationContext(
            plan=plan_for(action, DB_REGRESSION),
            hypothesis=DB_REGRESSION,
            signals=flagship_signals(),
            deployments=DEPLOYMENTS,
        ),
        sandbox=lambda _: healthy_sandbox(True),
    )
    assert result.status == ValidationStatus.ACCEPTED
    assert result.sandbox is not None


def test_rollback_rejected_when_sandbox_fails() -> None:
    action = RemediationAction(
        action_type=ActionType.ROLLBACK_DEPLOYMENT,
        service="checkout-service",
        environment="production",
        deployment_id="842",
    )
    result = ValidationEngine().validate(
        ValidationContext(
            plan=plan_for(action, DB_REGRESSION),
            hypothesis=DB_REGRESSION,
            signals=flagship_signals(),
            deployments=DEPLOYMENTS,
        ),
        sandbox=lambda _: healthy_sandbox(False),
    )
    assert result.status == ValidationStatus.REJECTED
    assert any(c.name.startswith("sandbox:") for c in result.failed_checks)


def test_rollback_of_non_active_deployment_is_rejected() -> None:
    action = RemediationAction(
        action_type=ActionType.ROLLBACK_DEPLOYMENT,
        service="checkout-service",
        environment="production",
        deployment_id="841",
    )
    result = ValidationEngine().validate(
        ValidationContext(
            plan=plan_for(action, DB_REGRESSION),
            hypothesis=DB_REGRESSION,
            signals=flagship_signals(),
            deployments=DEPLOYMENTS,
        )
    )
    assert result.status == ValidationStatus.REJECTED
    assert "deployment_is_active" in {c.name for c in result.failed_checks}


@pytest.mark.critical
@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE orders",
        "TRUNCATE TABLE orders",
        "DELETE FROM customers",
        "CREATE INDEX CONCURRENTLY idx ON customers (lower(email)); DROP TABLE orders",
        "ALTER TABLE orders DROP COLUMN id",
    ],
)
def test_destructive_sql_is_always_rejected(sql: str) -> None:
    action = RemediationAction(
        action_type=ActionType.DATABASE_REMEDIATION, service="checkout-service", environment="production", sql=sql
    )
    called = []
    result = ValidationEngine().validate(
        ValidationContext(plan=plan_for(action, DB_REGRESSION), hypothesis=DB_REGRESSION, signals=flagship_signals()),
        sandbox=lambda a: called.append(a) or healthy_sandbox(True),
    )  # type: ignore[func-returns-value]
    assert result.status == ValidationStatus.REJECTED
    assert not called, "destructive SQL must never be sent even to the sandbox"


@pytest.mark.critical
@pytest.mark.parametrize(
    "action_type",
    [
        ActionType.DROP_TABLE,
        ActionType.DELETE_DATABASE,
        ActionType.DESTROY_INFRASTRUCTURE,
        ActionType.ROTATE_CORE_CREDENTIALS,
        ActionType.DISABLE_SECURITY_CONTROLS,
        ActionType.REMOVE_BACKUPS,
    ],
)
def test_destructive_action_classes_are_rejected(action_type: ActionType) -> None:
    action = RemediationAction(action_type=action_type, service="checkout-service", environment="production")
    result = ValidationEngine().validate(
        ValidationContext(plan=plan_for(action, DB_REGRESSION), hypothesis=DB_REGRESSION, signals=flagship_signals())
    )
    assert result.status == ValidationStatus.REJECTED


def test_config_change_must_revert_or_be_documented() -> None:
    hyp = Hypothesis(title="config", category=HypothesisCategory.CONFIG_REGRESSION, description="d", probability=0.5)
    signals = flagship_signals(
        recent_config_changes=[
            ConfigChangeSignal(key="cache.ttl_seconds", old_value="300", new_value="0", minutes_ago=12)
        ],
        db_pool_utilization_pct=88,
    )
    revert = RemediationAction(
        action_type=ActionType.CONFIGURATION_CHANGE,
        service="inventory-service",
        environment="production",
        config_key="cache.ttl_seconds",
        config_value="300",
    )
    arbitrary = revert.model_copy(update={"config_value": "5"})
    engine = ValidationEngine()
    ok = engine.validate(
        ValidationContext(plan=plan_for(revert, hyp), hypothesis=hyp, signals=signals),
        sandbox=lambda _: healthy_sandbox(True),
    )
    bad = engine.validate(
        ValidationContext(plan=plan_for(arbitrary, hyp), hypothesis=hyp, signals=signals),
        sandbox=lambda _: healthy_sandbox(True),
    )
    assert ok.status == ValidationStatus.ACCEPTED
    assert bad.status == ValidationStatus.REJECTED


def test_documented_failover_config_change_is_accepted() -> None:
    hyp = Hypothesis(title="gateway", category=HypothesisCategory.DEPENDENCY_OUTAGE, description="d", probability=0.5)
    action = RemediationAction(
        action_type=ActionType.CONFIGURATION_CHANGE,
        service="payment-service",
        environment="production",
        config_key="payment.gateway.provider",
        config_value="secondary",
    )
    citation = DocumentCitation(
        document_id="rb",
        chunk_id="rb#1",
        title="failover",
        document_type="runbook",
        snippet="set payment.gateway.provider=secondary",
        vector_score=0.5,
        keyword_score=1,
        fused_score=0.03,
        rerank_score=0.9,
    )
    result = ValidationEngine().validate(
        ValidationContext(
            plan=plan_for(action, hyp),
            hypothesis=hyp,
            signals=flagship_signals(service="payment-service", recent_config_changes=[], db_pool_utilization_pct=40),
            citations=[citation],
        ),
        sandbox=lambda _: healthy_sandbox(True),
    )
    assert result.status == ValidationStatus.ACCEPTED


def test_security_sensitive_config_is_rejected() -> None:
    hyp = Hypothesis(title="x", category=HypothesisCategory.CONFIG_REGRESSION, description="d", probability=0.5)
    action = RemediationAction(
        action_type=ActionType.CONFIGURATION_CHANGE,
        service="payment-service",
        environment="production",
        config_key="security.auth.enabled",
        config_value="false",
    )
    result = ValidationEngine().validate(
        ValidationContext(plan=plan_for(action, hyp), hypothesis=hyp, signals=flagship_signals())
    )
    assert result.status == ValidationStatus.REJECTED
    assert {"key_is_mutable", "not_security_sensitive"} <= {c.name for c in result.failed_checks}
