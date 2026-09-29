"""Investigation worker: consumes start/resume jobs from Redis and runs the LangGraph workflow.

Graph state lives in the PostgreSQL checkpointer, so any worker replica can resume any incident.
"""

from __future__ import annotations

import logging
import signal
from types import FrameType

from veriops.api.runtime import RedisJobQueue, build_runtime
from veriops.db.seed import seed_knowledge
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
    try:
        seed_knowledge(runtime.platform.ingestor)
    except Exception:
        logger.exception("knowledge seeding failed")
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
