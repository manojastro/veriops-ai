"""Process-wide runtime: platform wiring, durable checkpointer and investigation job execution."""

from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Protocol

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from veriops.bootstrap import Platform, build_platform
from veriops.db.recorder import DbIncidentRecorder
from veriops.db.session import _session_factory, psycopg_conninfo
from veriops.domain.enums import IncidentStatus
from veriops.domain.models import Alert, ApprovalDecision
from veriops.graph.runner import InvestigationRunner
from veriops.graph.state import checkpoint_serializer
from veriops.rag.store import PgVectorStore
from veriops.shared.config import ExecutionMode, Settings, get_settings

logger = logging.getLogger(__name__)
JOB_QUEUE_KEY = "veriops:jobs"


class JobQueue(Protocol):
    def submit(self, job: dict[str, Any]) -> None: ...


@dataclass
class Runtime:
    settings: Settings
    platform: Platform
    runner: InvestigationRunner
    recorder: DbIncidentRecorder
    _pool: ConnectionPool | None = None
    _executor: ThreadPoolExecutor = field(default_factory=lambda: ThreadPoolExecutor(max_workers=4))
    _locks: dict[str, threading.Lock] = field(default_factory=dict)
    _locks_guard: threading.Lock = field(default_factory=threading.Lock)
    queue: JobQueue | None = None

    # --- job execution -------------------------------------------------------------------------------

    def _lock(self, incident_id: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(incident_id, threading.Lock())

    def run_job(self, job: dict[str, Any]) -> None:
        incident_id = job["incident_id"]
        with self._lock(incident_id):
            try:
                if job["type"] == "start":
                    self.runner.start(incident_id, Alert.model_validate(job["alert"]))
                elif job["type"] == "resume":
                    self.runner.resume(incident_id, ApprovalDecision.model_validate(job["decision"]))
            except Exception as exc:
                logger.exception("investigation job failed: %s", job.get("type"))
                self.recorder.incident_update(incident_id, status=IncidentStatus.FAILED.value)
                from veriops.domain.models import TimelineEvent

                self.recorder.timeline(
                    incident_id,
                    TimelineEvent(
                        type="job_failed", title="Investigation job failed", detail=str(exc), status="danger"
                    ),
                )

    def submit(self, job: dict[str, Any]) -> None:
        if self.queue is not None:
            self.queue.submit(job)
        else:
            self._executor.submit(self.run_job, job)

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
        if self._pool is not None:
            self._pool.close()


class RedisJobQueue:
    def __init__(self, redis_url: str) -> None:
        import redis

        self._redis = redis.Redis.from_url(redis_url)

    def submit(self, job: dict[str, Any]) -> None:
        self._redis.rpush(JOB_QUEUE_KEY, json.dumps(job, default=str))

    def pop(self, timeout: int = 5) -> dict[str, Any] | None:
        item = self._redis.blpop([JOB_QUEUE_KEY], timeout=timeout)
        return json.loads(item[1]) if item else None


def build_runtime(settings: Settings | None = None, *, for_worker: bool = False) -> Runtime:
    settings = settings or get_settings()
    recorder = DbIncidentRecorder()
    store = PgVectorStore(_session_factory(None))
    platform = build_platform(settings, store=store, recorder=recorder)
    platform.tools.add_observer(recorder)
    platform.llm.observers.append(recorder)
    pool: ConnectionPool | None = None
    if settings.use_postgres_checkpointer:
        pool = ConnectionPool(
            psycopg_conninfo(),
            max_size=10,
            open=True,
            kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        )
        checkpointer = PostgresSaver(pool, serde=checkpoint_serializer())  # type: ignore[arg-type]
        checkpointer.setup()
        runner = InvestigationRunner(platform.graph_deps(), checkpointer)
    else:
        runner = InvestigationRunner(platform.graph_deps())
    queue: JobQueue | None = None
    if settings.execution_mode == ExecutionMode.WORKER and not for_worker:
        queue = RedisJobQueue(settings.redis_url)
    return Runtime(settings=settings, platform=platform, runner=runner, recorder=recorder, _pool=pool, queue=queue)


_runtime: Runtime | None = None
_runtime_lock = threading.Lock()


def get_runtime() -> Runtime:
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            _runtime = build_runtime()
        return _runtime


def set_runtime(runtime: Runtime | None) -> None:
    global _runtime
    with _runtime_lock:
        _runtime = runtime
