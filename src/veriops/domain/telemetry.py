"""Telemetry and infrastructure models returned by tools.

These are the *contracts* between tools and the rest of the system. The simulator produces them
today; real integrations (Prometheus, Loki, Tempo, Kubernetes API, pg_stat_statements, GitHub)
would produce the same models.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class MetricPoint(BaseModel):
    ts: datetime
    p95_latency_ms: float
    error_rate_pct: float
    cpu_pct: float
    memory_pct: float
    db_pool_utilization_pct: float
    rps: float


class ServiceMetrics(BaseModel):
    service: str
    environment: str
    ts: datetime
    p95_latency_ms: float
    p99_latency_ms: float
    error_rate_pct: float
    rps: float
    cpu_pct: float
    memory_pct: float
    db_pool_utilization_pct: float
    replicas: int
    series: list[MetricPoint] = Field(default_factory=list)


class LogEntry(BaseModel):
    ts: datetime
    service: str
    level: str
    message: str
    trace_id: str | None = None
    attributes: dict[str, str | int | float] = Field(default_factory=dict)


class LogPattern(BaseModel):
    pattern: str
    count: int
    level: str
    first_seen: datetime


class LogSearchResult(BaseModel):
    service: str
    query: str
    total: int
    counts_by_level: dict[str, int]
    top_patterns: list[LogPattern]
    samples: list[LogEntry]


class TraceSpan(BaseModel):
    span_id: str
    parent_span_id: str | None
    service: str
    operation: str
    duration_ms: float
    status: str
    attributes: dict[str, str | int | float] = Field(default_factory=dict)


class Trace(BaseModel):
    trace_id: str
    root_service: str
    root_operation: str
    duration_ms: float
    status: str
    spans: list[TraceSpan]
    critical_path: list[str] = Field(default_factory=list)
    slowest_span: str | None = None


class PodStatus(BaseModel):
    name: str
    phase: str
    ready: bool
    restarts: int
    cpu_millicores: int
    memory_mb: int
    oom_killed: bool = False


class DeploymentStatus(BaseModel):
    service: str
    namespace: str
    desired_replicas: int
    ready_replicas: int
    image: str
    revision: str
    pods: list[PodStatus]
    hpa_min: int
    hpa_max: int


class DeploymentRecord(BaseModel):
    id: str
    service: str
    version: str
    image: str
    commit_sha: str
    author: str
    description: str
    deployed_at: datetime
    status: str  # active | superseded | rolled_back


class FileDiff(BaseModel):
    path: str
    additions: int
    deletions: int
    patch: str


class GitCommit(BaseModel):
    sha: str
    author: str
    message: str
    ts: datetime
    files_changed: list[str]


class GitDiff(BaseModel):
    sha: str
    files: list[FileDiff]


class DatabaseMetrics(BaseModel):
    database: str
    cpu_pct: float
    active_connections: int
    max_connections: int
    lock_waits: int
    avg_query_ms: float
    p95_query_ms: float
    qps: float


class SlowQuery(BaseModel):
    query_id: str
    fingerprint: str
    calls_per_min: int
    mean_ms: float
    baseline_mean_ms: float
    p95_ms: float
    rows_examined: int
    plan: str
    first_seen: datetime

    @property
    def regression_factor(self) -> float:
        return round(self.mean_ms / self.baseline_mean_ms, 1) if self.baseline_mean_ms else 0.0


class ConnectionPoolStats(BaseModel):
    service: str
    active_connections: int
    idle_connections: int
    max_connections: int
    waiting_requests: int
    utilization_percent: float
    avg_wait_ms: float
    timeouts_per_min: int


class ConfigChange(BaseModel):
    key: str
    old_value: str
    new_value: str
    changed_at: datetime
    changed_by: str


class DependencyHealth(BaseModel):
    name: str
    kind: str  # internal | external | database | cache
    healthy: bool
    p95_latency_ms: float
    error_rate_pct: float


class HealthCheckResult(BaseModel):
    service: str
    healthy: bool
    p95_latency_ms: float
    error_rate_pct: float
    db_pool_utilization_pct: float
    checks: dict[str, bool]
