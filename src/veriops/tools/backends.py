"""Infrastructure backends used by tools.

``InfrastructureBackend`` is the seam where real integrations plug in. Two implementations ship:

* ``HttpSimulatorBackend`` — talks to the simulator service over HTTP (used by the running stack).
* ``InProcessSimulatorBackend`` — calls the simulator world directly (unit tests, evaluations).

A production deployment would add e.g. ``PrometheusMetricsBackend``, ``KubernetesBackend`` and
``GitHubBackend`` implementing the same protocol.
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx

from veriops.domain.models import SandboxResult
from veriops.domain.telemetry import (
    ConfigChange,
    ConnectionPoolStats,
    DatabaseMetrics,
    DependencyHealth,
    DeploymentRecord,
    DeploymentStatus,
    GitCommit,
    GitDiff,
    HealthCheckResult,
    LogSearchResult,
    ServiceMetrics,
    SlowQuery,
    Trace,
)
from veriops.shared.errors import ToolError
from veriops.simulator.telemetry import TelemetryRenderer
from veriops.simulator.world import SimulatorActionError, SimulatorWorld


class InfrastructureBackend(Protocol):
    # read-only telemetry
    def metrics(self, service: str, window_minutes: int) -> ServiceMetrics: ...
    def logs(self, service: str, query: str, window_minutes: int) -> LogSearchResult: ...
    def trace(self, service: str, trace_id: str | None) -> Trace: ...
    def deployment_status(self, service: str) -> DeploymentStatus: ...
    def deployments(self, service: str, limit: int) -> list[DeploymentRecord]: ...
    def commit(self, sha: str) -> GitCommit: ...
    def diff(self, sha: str) -> GitDiff: ...
    def database_metrics(self, service: str) -> DatabaseMetrics: ...
    def slow_queries(self, service: str) -> list[SlowQuery]: ...
    def connection_pool(self, service: str) -> ConnectionPoolStats: ...
    def config_history(self, service: str) -> list[ConfigChange]: ...
    def dependencies(self, service: str) -> list[DependencyHealth]: ...
    def health(self, service: str) -> HealthCheckResult: ...
    def sandbox(self, service: str, action: dict[str, Any]) -> SandboxResult: ...
    # writes
    def scale(self, service: str, replicas: int) -> dict[str, Any]: ...
    def restart(self, service: str) -> dict[str, Any]: ...
    def rollback(self, service: str, deployment_id: str) -> dict[str, Any]: ...
    def restore(self, service: str, deployment_id: str) -> dict[str, Any]: ...
    def set_config(self, service: str, key: str, value: str) -> dict[str, Any]: ...
    def run_sql(self, service: str, sql: str) -> dict[str, Any]: ...


class InProcessSimulatorBackend:
    def __init__(self, world: SimulatorWorld) -> None:
        self.world = world
        self.render = TelemetryRenderer(world)

    def _guard(self, tool: str, fn: Any, *args: Any) -> Any:
        try:
            return fn(*args)
        except KeyError as exc:
            raise ToolError(tool, f"not found: {exc}") from exc
        except SimulatorActionError as exc:
            raise ToolError(tool, str(exc)) from exc

    def metrics(self, service: str, window_minutes: int) -> ServiceMetrics:
        return self._guard("metrics", self.render.metrics, service, window_minutes)  # type: ignore[no-any-return]

    def logs(self, service: str, query: str, window_minutes: int) -> LogSearchResult:
        return self._guard("logs", self.render.logs, service, query, window_minutes)  # type: ignore[no-any-return]

    def trace(self, service: str, trace_id: str | None) -> Trace:
        return self._guard("trace", self.render.trace, service, trace_id)  # type: ignore[no-any-return]

    def deployment_status(self, service: str) -> DeploymentStatus:
        return self._guard("k8s", self.render.deployment_status, service)  # type: ignore[no-any-return]

    def deployments(self, service: str, limit: int) -> list[DeploymentRecord]:
        return self._guard("deployments", self.render.deployments, service, limit)  # type: ignore[no-any-return]

    def commit(self, sha: str) -> GitCommit:
        return self._guard("git", self.render.commit, sha)  # type: ignore[no-any-return]

    def diff(self, sha: str) -> GitDiff:
        return self._guard("git", self.render.diff, sha)  # type: ignore[no-any-return]

    def database_metrics(self, service: str) -> DatabaseMetrics:
        return self._guard("db", self.render.database_metrics, service)  # type: ignore[no-any-return]

    def slow_queries(self, service: str) -> list[SlowQuery]:
        return self._guard("db", self.render.slow_queries, service)  # type: ignore[no-any-return]

    def connection_pool(self, service: str) -> ConnectionPoolStats:
        return ConnectionPoolStats(**self._guard("db", self.render.connection_pool, service))

    def config_history(self, service: str) -> list[ConfigChange]:
        return self._guard("config", self.render.config_history, service)  # type: ignore[no-any-return]

    def dependencies(self, service: str) -> list[DependencyHealth]:
        return self._guard("deps", self.render.dependencies, service)  # type: ignore[no-any-return]

    def health(self, service: str) -> HealthCheckResult:
        return self._guard("health", self.render.health, service)  # type: ignore[no-any-return]

    def sandbox(self, service: str, action: dict[str, Any]) -> SandboxResult:
        return self._guard("sandbox", self.render.sandbox, service, action)  # type: ignore[no-any-return]

    def scale(self, service: str, replicas: int) -> dict[str, Any]:
        return self._guard("scale", self.world.scale, service, replicas)  # type: ignore[no-any-return]

    def restart(self, service: str) -> dict[str, Any]:
        return self._guard("restart", self.world.restart, service)  # type: ignore[no-any-return]

    def rollback(self, service: str, deployment_id: str) -> dict[str, Any]:
        return self._guard("rollback", self.world.rollback, service, deployment_id)  # type: ignore[no-any-return]

    def restore(self, service: str, deployment_id: str) -> dict[str, Any]:
        return self._guard("restore", self.world.restore_deployment, service, deployment_id)  # type: ignore[no-any-return]

    def set_config(self, service: str, key: str, value: str) -> dict[str, Any]:
        return self._guard("config", self.world.set_config, service, key, value)  # type: ignore[no-any-return]

    def run_sql(self, service: str, sql: str) -> dict[str, Any]:
        return self._guard("sql", self.world.run_sql, service, sql)  # type: ignore[no-any-return]


class HttpSimulatorBackend:
    """HTTP client for the simulator. Transport errors and 5xx are retryable; 4xx are not."""

    def __init__(self, base_url: str, timeout_s: float = 10.0, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(base_url=base_url, timeout=timeout_s)

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise ToolError(path, f"timeout: {exc}", retryable=True) from exc
        except httpx.TransportError as exc:
            raise ToolError(path, f"transport error: {exc}", retryable=True) from exc
        if response.status_code >= 500:
            raise ToolError(path, f"backend error {response.status_code}", retryable=True)
        if response.status_code >= 400:
            detail = response.json().get("detail", response.text) if response.content else response.reason_phrase
            raise ToolError(path, f"{response.status_code}: {detail}")
        return response.json()

    def metrics(self, service: str, window_minutes: int) -> ServiceMetrics:
        return ServiceMetrics(**self._request("GET", f"/services/{service}/metrics", params={"window": window_minutes}))

    def logs(self, service: str, query: str, window_minutes: int) -> LogSearchResult:
        return LogSearchResult(
            **self._request("GET", f"/services/{service}/logs", params={"query": query, "window": window_minutes})
        )

    def trace(self, service: str, trace_id: str | None) -> Trace:
        params = {"trace_id": trace_id} if trace_id else {}
        return Trace(**self._request("GET", f"/services/{service}/trace", params=params))

    def deployment_status(self, service: str) -> DeploymentStatus:
        return DeploymentStatus(**self._request("GET", f"/services/{service}/k8s/deployment"))

    def deployments(self, service: str, limit: int) -> list[DeploymentRecord]:
        return [
            DeploymentRecord(**d)
            for d in self._request("GET", f"/services/{service}/deployments", params={"limit": limit})
        ]

    def commit(self, sha: str) -> GitCommit:
        return GitCommit(**self._request("GET", f"/git/commits/{sha}"))

    def diff(self, sha: str) -> GitDiff:
        return GitDiff(**self._request("GET", f"/git/commits/{sha}/diff"))

    def database_metrics(self, service: str) -> DatabaseMetrics:
        return DatabaseMetrics(**self._request("GET", f"/services/{service}/db/metrics"))

    def slow_queries(self, service: str) -> list[SlowQuery]:
        return [SlowQuery(**q) for q in self._request("GET", f"/services/{service}/db/slow-queries")]

    def connection_pool(self, service: str) -> ConnectionPoolStats:
        return ConnectionPoolStats(**self._request("GET", f"/services/{service}/db/pool"))

    def config_history(self, service: str) -> list[ConfigChange]:
        return [ConfigChange(**c) for c in self._request("GET", f"/services/{service}/config/history")]

    def dependencies(self, service: str) -> list[DependencyHealth]:
        return [DependencyHealth(**d) for d in self._request("GET", f"/services/{service}/dependencies")]

    def health(self, service: str) -> HealthCheckResult:
        return HealthCheckResult(**self._request("GET", f"/services/{service}/health"))

    def sandbox(self, service: str, action: dict[str, Any]) -> SandboxResult:
        return SandboxResult(**self._request("POST", f"/services/{service}/sandbox", json={"action": action}))

    def scale(self, service: str, replicas: int) -> dict[str, Any]:
        return self._request("POST", f"/services/{service}/actions/scale", json={"replicas": replicas})  # type: ignore[no-any-return]

    def restart(self, service: str) -> dict[str, Any]:
        return self._request("POST", f"/services/{service}/actions/restart")  # type: ignore[no-any-return]

    def rollback(self, service: str, deployment_id: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/services/{service}/actions/rollback",  # type: ignore[no-any-return]
            json={"deployment_id": deployment_id},
        )

    def restore(self, service: str, deployment_id: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/services/{service}/actions/restore",  # type: ignore[no-any-return]
            json={"deployment_id": deployment_id},
        )

    def set_config(self, service: str, key: str, value: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/services/{service}/actions/config",  # type: ignore[no-any-return]
            json={"key": key, "value": value},
        )

    def run_sql(self, service: str, sql: str) -> dict[str, Any]:
        return self._request("POST", f"/services/{service}/actions/sql", json={"sql": sql})  # type: ignore[no-any-return]

    # simulator administration (not exposed as agent tools)
    def activate_scenario(self, scenario_id: str) -> dict[str, Any]:
        return self._request("POST", f"/scenarios/{scenario_id}/activate")  # type: ignore[no-any-return]

    def list_scenarios(self) -> list[dict[str, Any]]:
        return self._request("GET", "/scenarios")  # type: ignore[no-any-return]

    def services_overview(self) -> list[dict[str, Any]]:
        return self._request("GET", "/services")  # type: ignore[no-any-return]
