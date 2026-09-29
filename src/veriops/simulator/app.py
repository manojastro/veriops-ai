"""HTTP API for the production simulator.

This service stands in for Prometheus, Loki, Tempo, the Kubernetes API, pg_stat_statements, the
deployment system and Git. VeriOps tools talk to it over HTTP through the same interface a real
integration would implement (see ``veriops.tools.backends``).
"""

from __future__ import annotations

import threading
from collections.abc import Awaitable, Callable
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from veriops.simulator.catalog import SERVICES
from veriops.simulator.scenarios import SCENARIOS
from veriops.simulator.telemetry import TelemetryRenderer
from veriops.simulator.world import WORLD, SimulatorActionError, SimulatorWorld


class ScaleRequest(BaseModel):
    replicas: int


class DeploymentRequest(BaseModel):
    deployment_id: str


class ConfigRequest(BaseModel):
    key: str
    value: str
    actor: str = "veriops"


class SqlRequest(BaseModel):
    sql: str


class SandboxRequest(BaseModel):
    action: dict[str, Any]


class TelemetryChaosRequest(BaseModel):
    fail_next: int = 1


class _Chaos:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.fail_next = 0

    def should_fail(self) -> bool:
        with self.lock:
            if self.fail_next > 0:
                self.fail_next -= 1
                return True
            return False


def create_simulator_app(world: SimulatorWorld | None = None) -> FastAPI:
    world = world or WORLD
    render = TelemetryRenderer(world)
    chaos = _Chaos()
    app = FastAPI(title="VeriOps Production Simulator", version="0.1.0")

    @app.middleware("http")
    async def telemetry_chaos(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        if request.method == "GET" and request.url.path.startswith("/services/") and chaos.should_fail():
            return JSONResponse(status_code=503, content={"detail": "injected telemetry backend failure"})
        return await call_next(request)

    @app.exception_handler(KeyError)
    async def _not_found(_: Request, exc: KeyError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc).strip("'\"")})

    @app.exception_handler(SimulatorActionError)
    async def _conflict(_: Request, exc: SimulatorActionError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/scenarios")
    def scenarios() -> list[dict[str, Any]]:
        return [
            {
                "id": s.id,
                "title": s.title,
                "service": s.service,
                "environment": s.environment,
                "severity": s.severity,
                "description": s.description,
            }
            for s in SCENARIOS.values()
        ]

    @app.get("/scenarios/{scenario_id}/ground-truth")
    def ground_truth(scenario_id: str) -> dict[str, Any]:
        truth = SCENARIOS[scenario_id].ground_truth
        return {
            "root_cause": truth.root_cause,
            "category": truth.category.value,
            "expected_action": truth.expected_action,
            "acceptable_actions": list(truth.acceptable_actions),
            "unsafe_actions": list(truth.unsafe_actions),
        }

    @app.post("/scenarios/{scenario_id}/activate")
    def activate(scenario_id: str) -> dict[str, Any]:
        if scenario_id not in SCENARIOS:
            raise HTTPException(404, f"unknown scenario {scenario_id}")
        return world.activate(scenario_id)

    @app.post("/reset")
    def reset(service: str | None = None) -> dict[str, str]:
        world.reset(service)
        return {"status": "reset"}

    @app.post("/chaos/telemetry")
    def telemetry_failures(req: TelemetryChaosRequest) -> dict[str, int]:
        with chaos.lock:
            chaos.fail_next = req.fail_next
        return {"fail_next": req.fail_next}

    @app.post("/services/{service}/chaos/fail-next-action")
    def fail_next_action(service: str) -> dict[str, str]:
        world.inject_verification_failure(service)
        return {"status": "armed"}

    @app.get("/services")
    def services() -> list[dict[str, Any]]:
        return [
            {
                "name": p.name,
                "team": p.team,
                "criticality": p.criticality,
                "description": p.description,
                "slo_p95_latency_ms": p.slo_p95_latency_ms,
                "slo_error_rate_pct": p.slo_error_rate_pct,
                "health": render.health(p.name).model_dump(mode="json"),
                "metrics": world.current_metrics(p.name),
                "replicas": world.state(p.name).replicas,
                "environment": world.state(p.name).environment,
            }
            for p in SERVICES.values()
        ]

    # --- telemetry (read-only) ------------------------------------------------------------------
    @app.get("/services/{service}/metrics")
    def metrics(service: str, window: int = 60) -> dict[str, Any]:
        return render.metrics(service, min(window, 360)).model_dump(mode="json")

    @app.get("/services/{service}/logs")
    def logs(service: str, query: str = "", window: int = 30) -> dict[str, Any]:
        return render.logs(service, query, min(window, 360)).model_dump(mode="json")

    @app.get("/services/{service}/trace")
    def trace(service: str, trace_id: str | None = None) -> dict[str, Any]:
        return render.trace(service, trace_id).model_dump(mode="json")

    @app.get("/services/{service}/k8s/deployment")
    def k8s_deployment(service: str) -> dict[str, Any]:
        return render.deployment_status(service).model_dump(mode="json")

    @app.get("/services/{service}/deployments")
    def deployments(service: str, limit: int = 10) -> list[dict[str, Any]]:
        return [d.model_dump(mode="json") for d in render.deployments(service, limit)]

    @app.get("/git/commits/{sha}")
    def commit(sha: str) -> dict[str, Any]:
        return render.commit(sha).model_dump(mode="json")

    @app.get("/git/commits/{sha}/diff")
    def diff(sha: str) -> dict[str, Any]:
        return render.diff(sha).model_dump(mode="json")

    @app.get("/services/{service}/db/metrics")
    def db_metrics(service: str) -> dict[str, Any]:
        return render.database_metrics(service).model_dump(mode="json")

    @app.get("/services/{service}/db/slow-queries")
    def slow_queries(service: str) -> list[dict[str, Any]]:
        return [q.model_dump(mode="json") for q in render.slow_queries(service)]

    @app.get("/services/{service}/db/pool")
    def pool(service: str) -> dict[str, Any]:
        return render.connection_pool(service)

    @app.get("/services/{service}/config/history")
    def config_history(service: str) -> list[dict[str, Any]]:
        return [c.model_dump(mode="json") for c in render.config_history(service)]

    @app.get("/services/{service}/dependencies")
    def dependencies(service: str) -> list[dict[str, Any]]:
        return [d.model_dump(mode="json") for d in render.dependencies(service)]

    @app.get("/services/{service}/health")
    def service_health(service: str) -> dict[str, Any]:
        return render.health(service).model_dump(mode="json")

    # --- actions (write) --------------------------------------------------------------------------
    @app.post("/services/{service}/actions/scale")
    def scale(service: str, req: ScaleRequest) -> dict[str, Any]:
        return world.scale(service, req.replicas)

    @app.post("/services/{service}/actions/restart")
    def restart(service: str) -> dict[str, Any]:
        return world.restart(service)

    @app.post("/services/{service}/actions/rollback")
    def rollback(service: str, req: DeploymentRequest) -> dict[str, Any]:
        return world.rollback(service, req.deployment_id)

    @app.post("/services/{service}/actions/restore")
    def restore(service: str, req: DeploymentRequest) -> dict[str, Any]:
        return world.restore_deployment(service, req.deployment_id)

    @app.post("/services/{service}/actions/config")
    def config(service: str, req: ConfigRequest) -> dict[str, Any]:
        return world.set_config(service, req.key, req.value, req.actor)

    @app.post("/services/{service}/actions/sql")
    def sql(service: str, req: SqlRequest) -> dict[str, Any]:
        return world.run_sql(service, req.sql)

    @app.post("/services/{service}/sandbox")
    def sandbox(service: str, req: SandboxRequest) -> dict[str, Any]:
        return render.sandbox(service, req.action).model_dump(mode="json")

    return app


app = create_simulator_app()


def run() -> None:
    import os

    uvicorn.run(
        app,
        host=os.environ.get("SIMULATOR_HOST", "0.0.0.0"),  # noqa: S104
        port=int(os.environ.get("SIMULATOR_PORT", "8100")),
    )
