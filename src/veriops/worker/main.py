"""Investigation worker: consumes start/resume jobs from Redis and runs the LangGraph workflow.

Graph state lives in the PostgreSQL checkpointer, so any worker replica can resume any incident.
"""

from __future__ import annotations

import logging
import os
import signal
from types import FrameType

from prometheus_client import start_http_server

from veriops.api.runtime import RedisJobQueue, build_runtime
from veriops.observability.metrics import REGISTRY
from veriops.observability.tracing import configure_tracing
from veriops.shared.config import get_settings
from veriops.shared.logging import configure_logging

logger = logging.getLogger(__name__)
_running = True


def _stop(signum: int, frame: FrameType | None) -> None:
    global _running
    _running = False
    logger.info("worker stopping (signal %s)", signum)


def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    if settings.otel_enabled:
        configure_tracing("veriops-worker", settings.otel_exporter_otlp_endpoint)
    runtime = build_runtime(settings, for_worker=True)
    # Knowledge seeding is owned by the API process to avoid concurrent first-boot ingestion.
    start_http_server(int(os.environ.get("WORKER_METRICS_PORT", "9100")), registry=REGISTRY)
    queue = RedisJobQueue(settings.redis_url)
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    logger.info("worker ready; waiting for jobs")
    while _running:
        job = queue.pop(timeout=2)
        if job is None:
            continue
        logger.info("processing %s job for %s", job.get("type"), job.get("incident_id"))
        runtime.run_job(job)
    runtime.close()


if __name__ == "__main__":
    run()
