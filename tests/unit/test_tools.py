"""Tool registry: allow-list, permissions, validation, retries, timeouts, caching."""

from __future__ import annotations

import time

import pytest
from pydantic import BaseModel

from veriops.domain.enums import ToolPermission as P
from veriops.shared.errors import ToolError
from veriops.simulator.world import SimulatorWorld
from veriops.tools.backends import InProcessSimulatorBackend
from veriops.tools.catalog import build_tool_registry
from veriops.tools.registry import ToolContext, ToolRegistry, ToolSpec

READ_CTX = ToolContext(incident_id="inc-1", actor="test")


@pytest.fixture
def registry() -> ToolRegistry:
    world = SimulatorWorld()
    world.activate("checkout_db_regression")
    return build_tool_registry(InProcessSimulatorBackend(world), max_retries=3)


def test_catalog_exposes_required_tools(registry: ToolRegistry) -> None:
    required = {
        "get_service_metrics",
        "search_logs",
        "get_trace",
        "get_pod_status",
        "get_deployment_status",
        "get_recent_deployments",
        "get_git_commit",
        "get_git_diff",
        "get_database_metrics",
        "get_slow_queries",
        "get_connection_pool_stats",
        "retrieve_runbook",
        "retrieve_similar_incidents",
        "restart_deployment",
        "scale_deployment",
        "rollback_deployment",
        "run_health_check",
    }
    assert required <= set(registry.names())
    descriptor = next(d for d in registry.descriptors() if d["name"] == "rollback_deployment")
    assert descriptor["annotations"]["permission"] == "HIGH_RISK_WRITE"
    assert "inputSchema" in descriptor


def test_structured_connection_pool_result(registry: ToolRegistry) -> None:
    result = registry.invoke("get_connection_pool_stats", {"service": "checkout-service"}, READ_CTX)
    assert result.ok
    assert result.data["active_connections"] == 100
    assert result.data["max_connections"] == 100
    assert result.data["utilization_percent"] == 100


def test_unknown_tool_is_rejected(registry: ToolRegistry) -> None:
    result = registry.invoke("run_shell", {"cmd": "rm -rf /"}, READ_CTX)
    assert not result.ok and "allow-list" in (result.error or "")


def test_invalid_arguments_rejected(registry: ToolRegistry) -> None:
    result = registry.invoke("get_service_metrics", {"service": "Checkout; DROP"}, READ_CTX)
    assert not result.ok and "invalid arguments" in (result.error or "")


def test_write_requires_grant(registry: ToolRegistry) -> None:
    result = registry.invoke("scale_deployment", {"service": "checkout-service", "replicas": 5}, READ_CTX)
    assert not result.ok and "not granted" in (result.error or "")


def test_high_risk_write_requires_approval(registry: ToolRegistry) -> None:
    ctx = ToolContext("inc-1", "t", frozenset({P.READ_ONLY, P.HIGH_RISK_WRITE}), approval_id=None)
    result = registry.invoke("rollback_deployment", {"service": "checkout-service", "deployment_id": "842"}, ctx)
    assert not result.ok and "approval" in (result.error or "")
    ctx = ToolContext("inc-1", "t", frozenset({P.READ_ONLY, P.HIGH_RISK_WRITE}), approval_id="human:alice")
    assert registry.invoke("rollback_deployment", {"service": "checkout-service", "deployment_id": "842"}, ctx).ok


@pytest.mark.critical
@pytest.mark.parametrize(
    "tool",
    [
        "drop_table",
        "delete_database",
        "destroy_infrastructure",
        "rotate_core_credentials",
        "disable_security_controls",
        "remove_backups",
    ],
)
def test_blocked_tools_never_execute(registry: ToolRegistry, tool: str) -> None:
    ctx = ToolContext("inc-1", "admin", frozenset(P), approval_id="human:admin")
    result = registry.invoke(tool, {"target": "orders"}, ctx)
    assert not result.ok and "BLOCKED" in (result.error or "")


@pytest.mark.critical
def test_sql_tool_refuses_destructive_statements(registry: ToolRegistry) -> None:
    ctx = ToolContext("inc-1", "admin", frozenset(P), approval_id="human:admin")
    for sql in ("DROP TABLE orders", "DELETE FROM orders", "CREATE INDEX CONCURRENTLY i ON t (c); DROP TABLE t"):
        assert not registry.invoke("execute_database_statement", {"service": "checkout-service", "sql": sql}, ctx).ok


class _Echo(BaseModel):
    value: int = 0


def test_retries_transient_failures_with_bounded_attempts() -> None:
    reg = ToolRegistry(max_retries=3, backoff_base_s=0.001)
    calls = {"n": 0}

    def flaky(p: _Echo) -> dict[str, int]:
        calls["n"] += 1
        if calls["n"] < 3:
            raise ToolError("flaky", "503", retryable=True)
        return {"value": p.value}

    reg.register(ToolSpec("flaky", "d", P.READ_ONLY, _Echo, flaky))
    result = reg.invoke("flaky", {"value": 7}, READ_CTX)
    assert result.ok and result.attempts == 3 and result.data == {"value": 7}

    calls["n"] = -10
    result = reg.invoke("flaky", {"value": 1}, READ_CTX)
    assert not result.ok and result.attempts == 3


def test_writes_are_not_retried() -> None:
    reg = ToolRegistry(max_retries=3, backoff_base_s=0.001)
    calls = {"n": 0}

    def write(p: _Echo) -> None:
        calls["n"] += 1
        raise ToolError("w", "timeout", retryable=True)

    reg.register(ToolSpec("w", "d", P.LOW_RISK_WRITE, _Echo, write))
    result = reg.invoke("w", {}, ToolContext("i", "a", frozenset({P.READ_ONLY, P.LOW_RISK_WRITE})))
    assert not result.ok and calls["n"] == 1


def test_timeout_is_enforced() -> None:
    reg = ToolRegistry(max_retries=1, default_timeout_s=0.05)
    reg.register(ToolSpec("slow", "d", P.READ_ONLY, _Echo, lambda p: time.sleep(0.5)))
    result = reg.invoke("slow", {}, READ_CTX)
    assert not result.ok and "timed out" in (result.error or "")


def test_cache_is_invalidated_by_writes(registry: ToolRegistry) -> None:
    first = registry.invoke("get_service_metrics", {"service": "checkout-service"}, READ_CTX)
    second = registry.invoke("get_service_metrics", {"service": "checkout-service"}, READ_CTX)
    assert second.cached and second.data["p95_latency_ms"] == first.data["p95_latency_ms"] == 4200
    ctx = ToolContext("i", "a", frozenset({P.READ_ONLY, P.HIGH_RISK_WRITE}), approval_id="human:alice")
    assert registry.invoke("rollback_deployment", {"service": "checkout-service", "deployment_id": "842"}, ctx).ok
    third = registry.invoke("get_service_metrics", {"service": "checkout-service"}, READ_CTX)
    assert not third.cached and third.data["p95_latency_ms"] == 310


def test_observer_receives_every_call(registry: ToolRegistry) -> None:
    seen = []

    class Obs:
        def on_tool_call(self, result, ctx):  # type: ignore[no-untyped-def]
            seen.append(result.tool)

    registry.add_observer(Obs())
    registry.invoke("get_trace", {"service": "checkout-service"}, READ_CTX)
    registry.invoke("drop_table", {}, READ_CTX)
    assert seen == ["get_trace", "drop_table"]
