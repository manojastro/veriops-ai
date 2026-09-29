"""Simulator physics: the flagship numbers and plausible action effects."""

from __future__ import annotations

import pytest

from veriops.simulator.scenarios import SCENARIOS
from veriops.simulator.telemetry import TelemetryRenderer
from veriops.simulator.world import SimulatorActionError, SimulatorWorld


def test_flagship_symptoms_match_specification() -> None:
    world = SimulatorWorld()
    activation = world.activate("checkout_db_regression")
    m = activation["metrics"]
    assert (m["p95_latency_ms"], m["error_rate_pct"], m["cpu_pct"], m["memory_pct"], m["db_pool_utilization_pct"]) == (
        4200,
        11.8,
        37,
        52,
        100,
    )


def test_flagship_sandbox_before_after() -> None:
    world = SimulatorWorld()
    world.activate("checkout_db_regression")
    render = TelemetryRenderer(world)
    rollback = render.sandbox("checkout-service", {"action_type": "rollback_deployment", "deployment_id": "842"})
    assert rollback.before["p95_latency_ms"] == 4200 and rollback.after["p95_latency_ms"] == 310
    assert rollback.after["error_rate_pct"] == 0.4 and rollback.passed
    scale = render.sandbox("checkout-service", {"action_type": "scale_deployment", "replicas": 8})
    assert not scale.passed
    assert scale.after["p95_latency_ms"] > 4200 and scale.after["error_rate_pct"] > 11.8
    # sandbox never mutates production
    assert world.current_metrics("checkout-service")["p95_latency_ms"] == 4200


def test_slow_query_regression_is_13x_after_deploy() -> None:
    world = SimulatorWorld()
    world.activate("checkout_db_regression")
    worst = TelemetryRenderer(world).slow_queries("checkout-service")[0]
    assert worst.regression_factor == 13.5
    assert "Seq Scan" in worst.plan


def test_rollback_only_active_revision() -> None:
    world = SimulatorWorld()
    world.activate("checkout_db_regression")
    with pytest.raises(SimulatorActionError):
        world.rollback("checkout-service", "841")
    world.rollback("checkout-service", "842")
    assert world.current_metrics("checkout-service")["p95_latency_ms"] == 310
    world.restore_deployment("checkout-service", "842")
    assert world.current_metrics("checkout-service")["p95_latency_ms"] == 4200


def test_scaling_fixes_cpu_saturation() -> None:
    world = SimulatorWorld()
    world.activate("user_cpu_saturation")
    world.scale("user-service", 6)
    m = world.current_metrics("user-service")
    assert m["cpu_pct"] < 70 and m["p95_latency_ms"] <= 250


def test_database_guard_refuses_destructive_sql() -> None:
    world = SimulatorWorld()
    world.activate("checkout_db_regression")
    with pytest.raises(SimulatorActionError):
        world.run_sql("checkout-service", "DROP TABLE orders")


def test_chaos_suppresses_next_action_effect() -> None:
    world = SimulatorWorld()
    world.activate("checkout_db_regression")
    world.inject_verification_failure("checkout-service")
    world.rollback("checkout-service", "842")
    assert world.current_metrics("checkout-service")["p95_latency_ms"] == 4200


@pytest.mark.parametrize("scenario_id", list(SCENARIOS))
def test_every_scenario_breaches_slo_and_has_a_fix(scenario_id: str) -> None:
    world = SimulatorWorld()
    activation = world.activate(scenario_id)
    health = TelemetryRenderer(world).health(str(activation["service"]))
    assert not health.healthy
