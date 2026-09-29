"""Deterministic signal extraction.

Turns raw evidence into a compact, typed set of facts ("signals"). Signals are computed by plain
Python — never by an LLM — and are shared by the LLM context, the hypothesis signal checks, the
validators and the risk engine, so every component reasons over the same numbers.
"""

from __future__ import annotations

import statistics
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from veriops.domain.enums import EvidenceKind
from veriops.domain.models import Alert, EvidenceItem
from veriops.shared.clock import utcnow

RECENT_DEPLOYMENT_MINUTES = 6 * 60
LOG_KEYWORDS = {
    "oom": ("oomkilled", "out of memory"),
    "gc_pressure": ("gc pause", "heap_used"),
    "pool_timeout": ("queuepool", "pooltimeout", "connection timed out", "couldn't get a connection"),
    "slow_query": ("slow query",),
    "exception": ("keyerror", "nullpointer", "exception", "typeerror", "taxserviceerror"),
    "upstream_timeout": ("readtimeout", "gateway timeout", "bad gateway", "504", "502"),
    "cache_miss": ("cache miss",),
    "saturation": ("queue depth", "saturated", "event loop lag"),
}


class RecentDeployment(BaseModel):
    id: str
    minutes_ago: float
    commit_sha: str
    description: str
    author: str


class ConfigChangeSignal(BaseModel):
    key: str
    old_value: str
    new_value: str
    minutes_ago: float


class IncidentSignals(BaseModel):
    service: str
    environment: str
    slo_p95_latency_ms: float
    slo_error_rate_pct: float
    p95_latency_ms: float
    error_rate_pct: float
    cpu_pct: float
    memory_pct: float
    db_pool_utilization_pct: float
    rps: float
    baseline_rps: float | None = None
    replicas: int | None = None
    hpa_max: int | None = None
    onset_minutes_ago: float | None = None
    latency_breach: bool
    error_breach: bool
    recent_deployment: RecentDeployment | None = None
    deployment_precedes_onset: bool = False
    log_keywords: list[str] = Field(default_factory=list)
    pod_restarts: int | None = None
    oom_killed: bool = False
    db_active_connections: int | None = None
    db_max_connections: int | None = None
    db_cpu_pct: float | None = None
    pool_per_pod: float | None = None
    slow_query_max_regression: float | None = None
    slow_query_after_deployment: bool | None = None
    unhealthy_dependencies: list[str] | None = None
    trace_slowest_span: str | None = None
    recent_config_changes: list[ConfigChangeSignal] | None = None
    git_diff_excerpt: list[str] | None = None
    collected: list[EvidenceKind] = Field(default_factory=list)
    evidence_refs: dict[str, str] = Field(default_factory=dict)


def latest_by_kind(evidence: dict[str, EvidenceItem]) -> dict[EvidenceKind, EvidenceItem]:
    latest: dict[EvidenceKind, EvidenceItem] = {}
    for item in sorted(evidence.values(), key=lambda e: e.collected_at):
        latest[item.kind] = item
    return latest


def _minutes_ago(ts: str | datetime, now: datetime) -> float:
    value = datetime.fromisoformat(ts) if isinstance(ts, str) else ts
    return round((now - value).total_seconds() / 60, 1)


def _onset(series: list[dict[str, Any]], now: datetime) -> float | None:
    if len(series) < 10:
        return None
    baseline = statistics.median(p["p95_latency_ms"] for p in series[:10])
    base_err = statistics.median(p["error_rate_pct"] for p in series[:10])
    base_mem = statistics.median(p["memory_pct"] for p in series[:10])
    for point in series:
        if (
            point["p95_latency_ms"] > baseline * 1.8
            or point["error_rate_pct"] > base_err * 4 + 1
            or point["memory_pct"] > base_mem * 1.25
        ):
            return _minutes_ago(point["ts"], now)
    return None


def extract_signals(
    alert: Alert, evidence: dict[str, EvidenceItem], slo: dict[str, float], now: datetime | None = None
) -> IncidentSignals:
    now = now or utcnow()
    by_kind = latest_by_kind(evidence)
    metrics = by_kind.get(EvidenceKind.METRICS)
    m = metrics.facts if metrics else {}
    series = m.get("series", [])
    values = {
        "p95_latency_ms": m.get("p95_latency_ms", alert.p95_latency_ms),
        "error_rate_pct": m.get("error_rate_pct", alert.error_rate_pct),
        "cpu_pct": m.get("cpu_pct", alert.cpu_pct),
        "memory_pct": m.get("memory_pct", alert.memory_pct),
        "db_pool_utilization_pct": m.get("db_pool_utilization_pct", alert.db_pool_utilization_pct),
        "rps": m.get("rps", 0.0),
    }
    signals = IncidentSignals(
        service=alert.service,
        environment=alert.environment,
        slo_p95_latency_ms=slo["p95_latency_ms"],
        slo_error_rate_pct=slo["error_rate_pct"],
        **values,
        baseline_rps=statistics.median(p["rps"] for p in series[:10]) if len(series) >= 10 else None,
        replicas=m.get("replicas"),
        onset_minutes_ago=_onset(series, now),
        latency_breach=values["p95_latency_ms"] > slo["p95_latency_ms"],
        error_breach=values["error_rate_pct"] > slo["error_rate_pct"],
        collected=sorted(by_kind.keys()),
        evidence_refs={k.value: v.id for k, v in by_kind.items()},
    )

    if (k8s := by_kind.get(EvidenceKind.KUBERNETES)) is not None:
        pods = k8s.facts.get("pods", [])
        signals.pod_restarts = sum(p.get("restarts", 0) for p in pods)
        signals.oom_killed = any(p.get("oom_killed") for p in pods)
        signals.hpa_max = k8s.facts.get("hpa_max")
        signals.replicas = signals.replicas or k8s.facts.get("desired_replicas")

    if (logs := by_kind.get(EvidenceKind.LOGS)) is not None:
        text = " ".join(p.get("pattern", "") for p in logs.facts.get("top_patterns", [])).lower()
        signals.log_keywords = sorted(k for k, needles in LOG_KEYWORDS.items() if any(n in text for n in needles))

    if (deps := by_kind.get(EvidenceKind.DEPLOYMENTS)) is not None:
        active = [d for d in deps.facts.get("deployments", []) if d.get("status") == "active"]
        if active:
            latest = active[0]
            age = _minutes_ago(latest["deployed_at"], now)
            if age <= RECENT_DEPLOYMENT_MINUTES:
                signals.recent_deployment = RecentDeployment(
                    id=latest["id"],
                    minutes_ago=age,
                    commit_sha=latest["commit_sha"],
                    description=latest["description"],
                    author=latest["author"],
                )
                if signals.onset_minutes_ago is not None:
                    gap = age - signals.onset_minutes_ago
                    signals.deployment_precedes_onset = -1 <= gap <= 30

    if (db := by_kind.get(EvidenceKind.DATABASE)) is not None:
        signals.db_active_connections = db.facts.get("active_connections")
        signals.db_max_connections = db.facts.get("max_connections")
        signals.db_cpu_pct = db.facts.get("cpu_pct")

    if (pool := by_kind.get(EvidenceKind.CONNECTION_POOL)) is not None and signals.replicas:
        signals.pool_per_pod = round(pool.facts.get("max_connections", 0) / signals.replicas, 2)

    if (slow := by_kind.get(EvidenceKind.SLOW_QUERIES)) is not None:
        queries = slow.facts.get("queries", [])
        factors = [q["mean_ms"] / q["baseline_mean_ms"] for q in queries if q.get("baseline_mean_ms")]
        signals.slow_query_max_regression = round(max(factors), 1) if factors else 1.0
        worst = max(queries, key=lambda q: q["mean_ms"] / max(q.get("baseline_mean_ms") or 1, 1e-6), default=None)
        if worst and signals.recent_deployment:
            signals.slow_query_after_deployment = (
                _minutes_ago(worst["first_seen"], now) <= signals.recent_deployment.minutes_ago + 1
                and signals.slow_query_max_regression >= 3
            )

    if (dep_health := by_kind.get(EvidenceKind.DEPENDENCIES)) is not None:
        signals.unhealthy_dependencies = [
            d["name"] for d in dep_health.facts.get("dependencies", []) if not d.get("healthy", True)
        ]

    if (trace := by_kind.get(EvidenceKind.TRACES)) is not None:
        signals.trace_slowest_span = trace.facts.get("slowest_span")

    if (cfg := by_kind.get(EvidenceKind.CONFIG_HISTORY)) is not None:
        signals.recent_config_changes = [
            ConfigChangeSignal(
                key=c["key"],
                old_value=c["old_value"],
                new_value=c["new_value"],
                minutes_ago=_minutes_ago(c["changed_at"], now),
            )
            for c in cfg.facts.get("changes", [])
            if _minutes_ago(c["changed_at"], now) <= RECENT_DEPLOYMENT_MINUTES
        ]

    if (diff := by_kind.get(EvidenceKind.GIT_DIFF)) is not None:
        lines: list[str] = []
        for f in diff.facts.get("files", []):
            lines += [ln for ln in f.get("patch", "").splitlines() if ln.startswith(("+", "-")) and len(ln) > 2]
        signals.git_diff_excerpt = lines[:12]

    return signals
