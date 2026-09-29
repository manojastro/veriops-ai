"""FastAPI application factory."""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from veriops import __version__
from veriops.api.routers import auth, evaluations, incidents, knowledge, platform
from veriops.api.runtime import get_runtime
from veriops.db.seed import seed_demo_users, seed_knowledge, seed_services
from veriops.observability import metrics as obs_metrics
from veriops.observability.tracing import configure_tracing
from veriops.security.auth import AuthError, decode_token
from veriops.security.ratelimit import InMemoryRateLimiter
from veriops.shared.config import get_settings
from veriops.shared.logging import configure_logging

logger = logging.getLogger(__name__)

DESCRIPTION = """
Autonomous production investigation with **verified** remediation.

AI proposes → deterministic systems verify → humans approve high-risk actions → the system executes →
production signals verify the result → automatic rollback when verification fails.

Authenticate with `POST /api/auth/login` and send `Authorization: Bearer <token>`.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    if settings.is_production:
        settings.validate_for_production()
    runtime = get_runtime()
    seed_services()
    seed_demo_users(settings)
    try:
        seed_knowledge(runtime.platform.ingestor)
    except Exception:
        logger.exception("knowledge seeding failed; retrieval will be degraded")
    logger.info(
        "VeriOps API started (llm=%s, execution=%s)", settings.llm_provider.value, settings.execution_mode.value
    )
    yield
    runtime.close()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="VeriOps AI",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        openapi_tags=[
            {"name": "incidents"},
            {"name": "knowledge"},
            {"name": "evaluations"},
            {"name": "auth"},
            {"name": "platform"},
            {"name": "simulator"},
        ],
    )
    if settings.otel_enabled:
        configure_tracing(settings.otel_service_name, settings.otel_exporter_otlp_endpoint)
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app, excluded_urls="health,metrics")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )

    limiter = InMemoryRateLimiter(settings.rate_limit_per_minute)
    login_limiter = InMemoryRateLimiter(10)

    @app.middleware("http")
    async def guard(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        started = time.perf_counter()
        path = request.url.path
        if path.startswith("/api/"):
            key = request.client.host if request.client else "unknown"
            header = request.headers.get("authorization", "")
            if header.lower().startswith("bearer "):
                with contextlib.suppress(AuthError):
                    key = f"user:{decode_token(header[7:], settings).user_id}"
            bucket = login_limiter if path == "/api/auth/login" else limiter
            allowed, retry_after = bucket.allow(key)
            if not allowed and not path.endswith("/stream"):
                return JSONResponse(
                    {"detail": "rate limit exceeded"},
                    status_code=429,
                    headers={"Retry-After": str(int(retry_after) + 1)},
                )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = response.headers.get("Cache-Control", "no-store")
        route = request.scope.get("route")
        route_path = getattr(route, "path", "unmatched")
        obs_metrics.HTTP_REQUESTS.labels(request.method, route_path, str(response.status_code)).inc()
        obs_metrics.HTTP_LATENCY.labels(request.method, route_path).observe(time.perf_counter() - started)
        return response

    for module in (platform, auth, incidents, knowledge, evaluations):
        app.include_router(module.router)
    return app


app = create_app()


def run() -> None:
    settings = get_settings()
    uvicorn.run("veriops.api.main:app", host=settings.api_host, port=settings.api_port, proxy_headers=True)
