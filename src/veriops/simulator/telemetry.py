"""Render simulator world state as telemetry contracts (``veriops.domain.telemetry``)."""

from __future__ import annotations

import hashlib
from datetime import timedelta

from veriops.domain.enums import HypothesisCategory as Fault
from veriops.domain.models import SandboxResult, SandboxTest
from veriops.domain.telemetry import (
    ConfigChange,
    DatabaseMetrics,
    DependencyHealth,
    DeploymentRecord,
    DeploymentStatus,
    FileDiff,
    GitCommit,
    GitDiff,
    HealthCheckResult,
    LogEntry,
    LogPattern,
    LogSearchResult,
    MetricPoint,
    PodStatus,
    ServiceMetrics,
    SlowQuery,
    Trace,
    TraceSpan,
)
from veriops.shared.clock import utcnow
from veriops.simulator.world import DB_PRESSURE_FAULTS, ServiceState, SimulatorWorld, compute_metrics


def _hex(*parts: object, n: int = 16) -> str:
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:n]


def _fault_active(state: ServiceState) -> bool:
    return state.fault is not None and state.fault.active and state.fault.mitigated_at is None


class TelemetryRenderer:
    def __init__(self, world: SimulatorWorld) -> None:
        self.world = world

    # --- metrics ---------------------------------------------------------------------------

    def metrics(self, service: str, window_minutes: int = 60) -> ServiceMetrics:
        state = self.world.state(service)
        current = compute_metrics(state)
        series = [
            MetricPoint(ts=ts, **values)  # type: ignore[arg-type]
            for ts, values in self.world.series(service, window_minutes)
        ]
        return ServiceMetrics(
            service=service,
            environment=state.environment,
            ts=utcnow(),
            p95_latency_ms=current["p95_latency_ms"],
            p99_latency_ms=round(current["p95_latency_ms"] * 1.6, 1),
            error_rate_pct=current["error_rate_pct"],
            rps=current["rps"],
            cpu_pct=current["cpu_pct"],
            memory_pct=current["memory_pct"],
            db_pool_utilization_pct=current["db_pool_utilization_pct"],
            replicas=state.replicas,
            series=series,
        )

    # --- logs ------------------------------------------------------------------------------

    def logs(self, service: str, query: str = "", window_minutes: int = 30) -> LogSearchResult:
        state = self.world.state(service)
        now = utcnow()
        metrics = compute_metrics(state)
        patterns: list[tuple[str, str]] = [
            (
                "INFO",
                f"{state.profile.root_operation} 200 duration_ms={int(state.profile.healthy['p95_latency_ms'] * 0.6)}",
            )
        ]
        fault_start = now - timedelta(minutes=window_minutes)
        if _fault_active(state) and state.fault is not None:
            patterns = list(state.fault.spec.log_patterns) + patterns
            fault_start = max(fault_start, state.fault.started_at)

        terms = [t.strip().lower() for t in query.split(" OR ") if t.strip() and t.strip() != "*"]

        def matches(level: str, message: str) -> bool:
            if not terms:
                return True
            text = f"{level} {message}".lower()
            return any(term in text for term in terms)

        rps = metrics["rps"]
        error_lines = int(rps * 60 * window_minutes * metrics["error_rate_pct"] / 100)
        entries: list[LogEntry] = []
        found: list[LogPattern] = []
        counts: dict[str, int] = {}
        for index, (level, message) in enumerate(patterns):
            if not matches(level, message):
                continue
            weight = 1.0 / (index + 1)
            count = max(1, int(error_lines * weight)) if level != "INFO" else int(rps * 60 * window_minutes)
            counts[level] = counts.get(level, 0) + count
            found.append(LogPattern(pattern=message, count=count, level=level, first_seen=fault_start))
            for k in range(3):
                ts = now - timedelta(seconds=17 * (k + 1) + index * 5)
                entries.append(
                    LogEntry(
                        ts=ts,
                        service=service,
                        level=level,
                        message=message,
                        trace_id=_hex(service, "trace", index, k, n=32),
                        attributes={"pod": f"{service}-{_hex(service, 'pod', n=5)}-{k % max(1, state.replicas)}"},
                    )
                )
        entries.sort(key=lambda e: e.ts, reverse=True)
        return LogSearchResult(
            service=service,
            query=query or "*",
            total=sum(counts.values()),
            counts_by_level=counts,
            top_patterns=sorted(found, key=lambda p: p.count, reverse=True)[:10],
            samples=entries[:20],
        )

    # --- traces ----------------------------------------------------------------------------

    def trace(self, service: str, trace_id: str | None = None) -> Trace:
        state = self.world.state(service)
        metrics = compute_metrics(state)
        total = metrics["p95_latency_ms"]
        trace_id = trace_id or _hex(service, "slow-trace", n=32)
        root_id = _hex(trace_id, "root", n=16)
        spans = [
            TraceSpan(
                span_id=root_id,
                parent_span_id=None,
                service=service,
                operation=state.profile.root_operation,
                duration_ms=total,
                status="ERROR" if metrics["error_rate_pct"] > 5 else "OK",
            )
        ]
        slow = state.fault.spec.slow_span if _fault_active(state) and state.fault else None
        children = [(dep, f"call {dep}") for dep in state.profile.dependencies]
        if slow is not None and slow not in children:
            children.insert(0, slow)
        remaining = total * 0.9
        for index, (svc, op) in enumerate(children):
            is_slow = slow is not None and (svc, op) == slow
            duration = total * 0.82 if is_slow else max(1.0, remaining * 0.08)
            attributes: dict[str, str | int | float] = {}
            if svc.endswith("-db"):
                attributes["db.system"] = "postgresql"
                attributes["db.statement"] = (
                    state.fault.spec.slow_query.fingerprint
                    if is_slow and state.fault and state.fault.spec.slow_query
                    else state.profile.db_statement
                )
            if is_slow and state.fault and state.fault.spec.fault == Fault.DEPENDENCY_OUTAGE:
                attributes["error.type"] = "ReadTimeout"
            spans.append(
                TraceSpan(
                    span_id=_hex(trace_id, index, n=16),
                    parent_span_id=root_id,
                    service=svc,
                    operation=op,
                    duration_ms=round(duration, 1),
                    status="ERROR" if is_slow and metrics["error_rate_pct"] > 5 else "OK",
                    attributes=attributes,
                )
            )
        slowest = max(spans[1:], key=lambda s: s.duration_ms)
        return Trace(
            trace_id=trace_id,
            root_service=service,
            root_operation=state.profile.root_operation,
            duration_ms=total,
            status=spans[0].status,
            spans=spans,
            critical_path=[spans[0].operation, f"{slowest.service}: {slowest.operation}"],
            slowest_span=f"{slowest.service}: {slowest.operation} ({slowest.duration_ms:.0f} ms)",
        )

    # --- kubernetes ------------------------------------------------------------------------

    def deployment_status(self, service: str) -> DeploymentStatus:
        state = self.world.state(service)
        metrics = compute_metrics(state)
        active = state.active_deployment
        oom = bool(_fault_active(state) and state.fault and state.fault.spec.oom_killed)
        pods = []
        for i in range(state.replicas):
            restarts = state.pod_restarts // state.replicas + (1 if i < state.pod_restarts % state.replicas else 0)
            pods.append(
                PodStatus(
                    name=f"{service}-{_hex(service, active.spec.id if active else '', n=6)}-{_hex(service, i, n=5)}",
                    phase="Running",
                    ready=not (oom and i == 0),
                    restarts=restarts,
                    cpu_millicores=int(metrics["cpu_pct"] * 10 * (1 + 0.1 * _noise_i(service, i))),
                    memory_mb=int(metrics["memory_pct"] / 100 * 1024),
                    oom_killed=oom and restarts > 0,
                )
            )
        return DeploymentStatus(
            service=service,
            namespace=state.environment,
            desired_replicas=state.replicas,
            ready_replicas=sum(p.ready for p in pods),
            image=f"registry.local/{service}:{active.spec.version if active else 'unknown'}",
            revision=active.spec.id if active else "unknown",
            pods=pods,
            hpa_min=state.profile.hpa_min,
            hpa_max=state.profile.hpa_max,
        )

    def deployments(self, service: str, limit: int = 10) -> list[DeploymentRecord]:
        state = self.world.state(service)
        records = [
            DeploymentRecord(
                id=d.spec.id,
                service=service,
                version=d.spec.version,
                image=f"registry.local/{service}:{d.spec.version}",
                commit_sha=d.spec.commit_sha,
                author=d.spec.author,
                description=d.spec.description,
                deployed_at=d.deployed_at,
                status=d.status,
            )
            for d in state.deployments
        ]
        return sorted(records, key=lambda r: r.deployed_at, reverse=True)[:limit]

    # --- git ---------------------------------------------------------------------------------

    def _find_deployment(self, sha: str):  # type: ignore[no-untyped-def]
        for state in self.world.services.values():
            for d in state.deployments:
                if d.spec.commit_sha == sha:
                    return d
        raise KeyError(f"commit {sha} not found")

    def commit(self, sha: str) -> GitCommit:
        d = self._find_deployment(sha)
        return GitCommit(
            sha=sha,
            author=d.spec.author,
            message=d.spec.description,
            ts=d.deployed_at - timedelta(minutes=40),
            files_changed=[f.path for f in d.spec.files],
        )

    def diff(self, sha: str) -> GitDiff:
        d = self._find_deployment(sha)
        return GitDiff(
            sha=sha,
            files=[
                FileDiff(path=f.path, additions=f.additions, deletions=f.deletions, patch=f.patch) for f in d.spec.files
            ],
        )

    # --- database --------------------------------------------------------------------------

    def database_metrics(self, service: str) -> DatabaseMetrics:
        state = self.world.state(service)
        active, max_conn = self.world.db_connections(state)
        spec = state.fault.spec if _fault_active(state) and state.fault else None
        pressure = spec is not None and spec.fault in DB_PRESSURE_FAULTS
        slow = spec.slow_query if spec else None
        cpu_pct = 88.0 if slow else (79.0 if pressure else 24.0)
        return DatabaseMetrics(
            database=state.profile.database,
            cpu_pct=cpu_pct,
            active_connections=active,
            max_connections=max_conn,
            lock_waits=37 if slow else 0,
            avg_query_ms=round(slow.mean_ms * 0.6, 1) if slow else 4.2,
            p95_query_ms=slow.p95_ms if slow else 11.0,
            qps=4120.0 if pressure and not slow else 980.0,
        )

    def slow_queries(self, service: str) -> list[SlowQuery]:
        state = self.world.state(service)
        now = utcnow()
        baseline = SlowQuery(
            query_id=_hex(service, "q-base", n=10),
            fingerprint=state.profile.db_statement,
            calls_per_min=9_000,
            mean_ms=6.1,
            baseline_mean_ms=5.9,
            p95_ms=14.0,
            rows_examined=1,
            plan="Index Scan using primary key",
            first_seen=now - timedelta(days=90),
        )
        spec = state.fault.spec if _fault_active(state) and state.fault else None
        if spec is None or spec.slow_query is None:
            return [baseline]
        sq = spec.slow_query
        first_seen = state.fault.started_at if state.fault else now
        return [
            SlowQuery(
                query_id=_hex(service, sq.fingerprint, n=10),
                fingerprint=sq.fingerprint,
                calls_per_min=sq.calls_per_min,
                mean_ms=sq.mean_ms,
                baseline_mean_ms=sq.baseline_ms,
                p95_ms=sq.p95_ms,
                rows_examined=sq.rows_examined,
                plan=sq.plan,
                first_seen=first_seen,
            ),
            baseline,
        ]

    def connection_pool(self, service: str) -> dict[str, object]:
        state = self.world.state(service)
        metrics = compute_metrics(state)
        _, pool_max = self.world.pool_limits(state)
        util = metrics["db_pool_utilization_pct"]
        active = round(util / 100 * pool_max)
        saturated = util >= 95
        return {
            "service": service,
            "active_connections": active,
            "idle_connections": pool_max - active,
            "max_connections": pool_max,
            "waiting_requests": int(metrics["rps"] * 0.15) if saturated else 0,
            "utilization_percent": util,
            "avg_wait_ms": 4800.0 if saturated else 2.0,
            "timeouts_per_min": int(metrics["error_rate_pct"] / 100 * metrics["rps"] * 60 * 0.8) if saturated else 0,
        }

    def config_history(self, service: str) -> list[ConfigChange]:
        return sorted(self.world.state(service).config_history, key=lambda c: c.changed_at, reverse=True)

    def dependencies(self, service: str) -> list[DependencyHealth]:
        state = self.world.state(service)
        spec = state.fault.spec if _fault_active(state) and state.fault else None
        failing = spec.failing_dependency if spec else None
        if failing and state.config.get("payment.gateway.provider") == "secondary":
            failing = None
        slow_db = spec is not None and spec.slow_query is not None
        deps = []
        for name in state.profile.dependencies:
            kind = "database" if name.endswith("-db") else ("cache" if "cache" in name else "internal")
            if name == "acme-pay-gateway":
                kind = "external"
            unhealthy = name == failing
            p95 = 3000.0 if unhealthy else (410.0 if slow_db and kind == "database" else 12.0)
            deps.append(
                DependencyHealth(
                    name=name,
                    kind=kind,
                    healthy=not unhealthy,
                    p95_latency_ms=p95,
                    error_rate_pct=62.0 if unhealthy else 0.1,
                )
            )
        return deps

    def health(self, service: str) -> HealthCheckResult:
        state = self.world.state(service)
        return health_from_metrics(state, compute_metrics(state), self.dependencies(service))

    # --- sandbox -----------------------------------------------------------------------------

    def sandbox(self, service: str, action: dict[str, object]) -> SandboxResult:
        before, after, clone = self.world.sandbox(service, action)
        profile = clone.profile
        deps_after = TelemetryRenderer(_SingleServiceWorld(self.world, clone)).dependencies(service)
        db_active_after, db_max = self.world.db_connections(clone)
        tests = [
            SandboxTest(
                name="health_endpoint",
                passed=after["error_rate_pct"] < 5,
                threshold="5xx rate < 5%",
                observed=f"{after['error_rate_pct']:.1f}%",
            ),
            SandboxTest(
                name="latency_threshold",
                passed=after["p95_latency_ms"] <= profile.slo_p95_latency_ms,
                threshold=f"p95 <= {profile.slo_p95_latency_ms:.0f} ms",
                observed=f"{after['p95_latency_ms']:.0f} ms",
            ),
            SandboxTest(
                name="error_rate_threshold",
                passed=after["error_rate_pct"] <= profile.slo_error_rate_pct,
                threshold=f"error rate <= {profile.slo_error_rate_pct}%",
                observed=f"{after['error_rate_pct']:.2f}%",
            ),
            SandboxTest(
                name="db_connection_utilization",
                passed=after["db_pool_utilization_pct"] <= 85 and db_active_after <= db_max,
                threshold=f"pool <= 85% and db connections <= {db_max}",
                observed=f"pool {after['db_pool_utilization_pct']:.0f}%, db {db_active_after}/{db_max}",
            ),
            SandboxTest(
                name="dependency_health",
                passed=all(d.healthy for d in deps_after),
                threshold="all dependencies healthy",
                observed=", ".join(f"{d.name}={'ok' if d.healthy else 'FAIL'}" for d in deps_after),
            ),
            SandboxTest(
                name="functional_test",
                passed=after["error_rate_pct"] < 2 and after["p95_latency_ms"] < 2 * profile.slo_p95_latency_ms,
                threshold=f"synthetic {profile.root_operation} succeeds",
                observed="pass" if after["error_rate_pct"] < 2 else "fail",
            ),
        ]
        return SandboxResult(
            action=str(action.get("action_type")),
            before=before,
            after=after,
            tests=tests,
            passed=all(t.passed for t in tests),
        )


def health_from_metrics(
    state: ServiceState, metrics: dict[str, float], deps: list[DependencyHealth]
) -> HealthCheckResult:
    profile = state.profile
    checks = {
        "http_health": metrics["error_rate_pct"] < 5,
        "latency_slo": metrics["p95_latency_ms"] <= profile.slo_p95_latency_ms,
        "error_rate_slo": metrics["error_rate_pct"] <= profile.slo_error_rate_pct,
        "db_pool": metrics["db_pool_utilization_pct"] <= 85,
        "dependencies": all(d.healthy for d in deps),
    }
    return HealthCheckResult(
        service=profile.name,
        healthy=all(checks.values()),
        p95_latency_ms=metrics["p95_latency_ms"],
        error_rate_pct=metrics["error_rate_pct"],
        db_pool_utilization_pct=metrics["db_pool_utilization_pct"],
        checks=checks,
    )


def _noise_i(service: str, i: int) -> float:
    return (int(_hex(service, "cpu", i, n=2), 16) / 255.0) - 0.5


class _SingleServiceWorld(SimulatorWorld):
    """A read-only view exposing one (sandboxed) service state through the world interface."""

    def __init__(self, parent: SimulatorWorld, state: ServiceState) -> None:
        self._parent = parent
        self.services = {state.profile.name: state}
        self._lock = parent._lock
