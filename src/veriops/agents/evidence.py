"""Evidence Agent: collects evidence through tools and turns tool output into ``EvidenceItem``s.

Evidence collection is deterministic (a fixed mapping from evidence kind to tool calls). The LLM
decides *which hypotheses* to test; the coordinator decides *which evidence is missing*; this agent
only fetches and summarises.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from veriops.domain.enums import EvidenceKind
from veriops.domain.models import EvidenceItem
from veriops.tools.registry import ToolContext, ToolRegistry, ToolResult

E = EvidenceKind
LOG_QUERY = "error OR warn OR timeout OR exception OR oomkilled"


@dataclass
class CollectionOutcome:
    items: list[EvidenceItem]
    errors: list[str]
    tool_results: list[ToolResult]


def _metrics_summary(d: dict[str, Any]) -> str:
    return (
        f"p95 {d['p95_latency_ms']:.0f} ms, errors {d['error_rate_pct']:.1f}%, CPU {d['cpu_pct']:.0f}%, "
        f"memory {d['memory_pct']:.0f}%, DB pool {d['db_pool_utilization_pct']:.0f}%, {d['replicas']} replicas"
    )


class EvidenceAgent:
    name = "evidence_agent"

    def __init__(self, tools: ToolRegistry) -> None:
        self.tools = tools
        self._collectors: dict[
            EvidenceKind, Callable[[str, dict[EvidenceKind, EvidenceItem], ToolContext], CollectionOutcome]
        ] = {
            E.METRICS: self._metrics,
            E.LOGS: self._logs,
            E.TRACES: self._traces,
            E.KUBERNETES: self._kubernetes,
            E.DATABASE: self._database,
            E.CONNECTION_POOL: self._database,
            E.DEPLOYMENTS: self._deployments,
            E.GIT_COMMIT: self._git_commit,
            E.GIT_DIFF: self._git_diff,
            E.SLOW_QUERIES: self._slow_queries,
            E.CONFIG_HISTORY: self._config,
            E.DEPENDENCIES: self._dependencies,
        }

    def collect(
        self, kind: EvidenceKind, service: str, existing: dict[EvidenceKind, EvidenceItem], ctx: ToolContext
    ) -> CollectionOutcome:
        collector = self._collectors.get(kind)
        if collector is None:
            return CollectionOutcome([], [f"no collector for {kind.value}"], [])
        return collector(service, existing, ctx)

    # --- helpers --------------------------------------------------------------------------------

    def _call(self, tool: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return self.tools.invoke(tool, args, ctx)

    @staticmethod
    def _one(
        kind: EvidenceKind,
        result: ToolResult,
        summary: Callable[[Any], str],
        facts: Callable[[Any], dict[str, Any]] | None = None,
    ) -> CollectionOutcome:
        if not result.ok:
            return CollectionOutcome([], [f"{result.tool}: {result.error}"], [result])
        item = EvidenceItem(
            kind=kind,
            source_tool=result.tool,
            summary=summary(result.data),
            facts=facts(result.data) if facts else result.data,
        )
        return CollectionOutcome([item], [], [result])

    # --- collectors -----------------------------------------------------------------------------

    def _metrics(self, service: str, _: Any, ctx: ToolContext) -> CollectionOutcome:
        r = self._call("get_service_metrics", {"service": service, "window_minutes": 60}, ctx)
        return self._one(E.METRICS, r, _metrics_summary)

    def _logs(self, service: str, _: Any, ctx: ToolContext) -> CollectionOutcome:
        r = self._call("search_logs", {"service": service, "query": LOG_QUERY, "window_minutes": 30}, ctx)

        def summary(d: dict[str, Any]) -> str:
            top = d["top_patterns"][0]["pattern"] if d["top_patterns"] else "no matching lines"
            counts = ", ".join(f"{k} {v}" for k, v in d["counts_by_level"].items()) or "none"
            return f"{counts}; top pattern: {top[:140]}"

        return self._one(E.LOGS, r, summary)

    def _traces(self, service: str, _: Any, ctx: ToolContext) -> CollectionOutcome:
        r = self._call("get_trace", {"service": service}, ctx)
        return self._one(
            E.TRACES,
            r,
            lambda d: f"trace {d['trace_id'][:8]} {d['duration_ms']:.0f} ms; slowest span {d['slowest_span']}",
        )

    def _kubernetes(self, service: str, _: Any, ctx: ToolContext) -> CollectionOutcome:
        r = self._call("get_deployment_status", {"service": service}, ctx)

        def summary(d: dict[str, Any]) -> str:
            restarts = sum(p["restarts"] for p in d["pods"])
            oom = sum(1 for p in d["pods"] if p["oom_killed"])
            return (
                f"{d['ready_replicas']}/{d['desired_replicas']} pods ready, revision #{d['revision']}, "
                f"{restarts} restarts, {oom} OOMKilled, HPA {d['hpa_min']}-{d['hpa_max']}"
            )

        return self._one(E.KUBERNETES, r, summary)

    def _database(
        self, service: str, existing: dict[EvidenceKind, EvidenceItem], ctx: ToolContext
    ) -> CollectionOutcome:
        db = self._one(
            E.DATABASE,
            self._call("get_database_metrics", {"service": service}, ctx),
            lambda d: (
                f"{d['database']}: {d['active_connections']}/{d['max_connections']} connections, "
                f"CPU {d['cpu_pct']:.0f}%, avg query {d['avg_query_ms']:.1f} ms, "
                f"{d['lock_waits']} lock waits"
            ),
        )
        pool = self._one(
            E.CONNECTION_POOL,
            self._call("get_connection_pool_stats", {"service": service}, ctx),
            lambda d: (
                f"pool {d['utilization_percent']:.0f}% ({d['active_connections']}/"
                f"{d['max_connections']}), {d['waiting_requests']} waiting, avg wait "
                f"{d['avg_wait_ms']:.0f} ms, {d['timeouts_per_min']} timeouts/min"
            ),
        )
        return CollectionOutcome(db.items + pool.items, db.errors + pool.errors, db.tool_results + pool.tool_results)

    def _deployments(self, service: str, _: Any, ctx: ToolContext) -> CollectionOutcome:
        r = self._call("get_recent_deployments", {"service": service, "limit": 5}, ctx)

        def summary(d: list[dict[str, Any]]) -> str:
            active = next((x for x in d if x["status"] == "active"), None)
            if not active:
                return "no active deployment found"
            return (
                f"active deployment #{active['id']} {active['version']} by {active['author']}: {active['description']}"
            )

        return self._one(E.DEPLOYMENTS, r, summary, lambda d: {"deployments": d})

    @staticmethod
    def _active_commit(existing: dict[EvidenceKind, EvidenceItem]) -> tuple[str, str] | None:
        deps = existing.get(E.DEPLOYMENTS)
        if not deps:
            return None
        active = next((d for d in deps.facts.get("deployments", []) if d["status"] == "active"), None)
        return (active["id"], active["commit_sha"]) if active else None

    def _git_commit(
        self, service: str, existing: dict[EvidenceKind, EvidenceItem], ctx: ToolContext
    ) -> CollectionOutcome:
        target = self._active_commit(existing)
        if not target:
            return CollectionOutcome([], ["no deployment to inspect"], [])
        deployment_id, sha = target
        r = self._call("get_git_commit", {"sha": sha}, ctx)
        return self._one(
            E.GIT_COMMIT,
            r,
            lambda d: (
                f"commit {d['sha']} (#{deployment_id}) by {d['author']}: "
                f"{d['message']} [{', '.join(d['files_changed'])}]"
            ),
            lambda d: {**d, "deployment_id": deployment_id},
        )

    def _git_diff(
        self, service: str, existing: dict[EvidenceKind, EvidenceItem], ctx: ToolContext
    ) -> CollectionOutcome:
        target = self._active_commit(existing)
        if not target:
            return CollectionOutcome([], ["no deployment to diff"], [])
        deployment_id, sha = target
        r = self._call("get_git_diff", {"sha": sha}, ctx)

        def summary(d: dict[str, Any]) -> str:
            changed = [ln for f in d["files"] for ln in f["patch"].splitlines() if ln.startswith("+") and len(ln) > 3]
            return f"diff of #{deployment_id} ({sha}): " + (changed[0][:160] if changed else "no code changes")

        return self._one(E.GIT_DIFF, r, summary, lambda d: {**d, "deployment_id": deployment_id})

    def _slow_queries(self, service: str, _: Any, ctx: ToolContext) -> CollectionOutcome:
        r = self._call("get_slow_queries", {"service": service}, ctx)

        def summary(d: list[dict[str, Any]]) -> str:
            worst = max(d, key=lambda q: q["mean_ms"] / max(q["baseline_mean_ms"], 1e-6))
            factor = worst["mean_ms"] / max(worst["baseline_mean_ms"], 1e-6)
            return (
                f"worst query {factor:.1f}x baseline ({worst['baseline_mean_ms']:.0f} -> {worst['mean_ms']:.0f} ms): "
                f"{worst['fingerprint'][:90]}; plan: {worst['plan'][:80]}"
            )

        return self._one(E.SLOW_QUERIES, r, summary, lambda d: {"queries": d})

    def _config(self, service: str, _: Any, ctx: ToolContext) -> CollectionOutcome:
        r = self._call("get_config_history", {"service": service}, ctx)
        return self._one(
            E.CONFIG_HISTORY,
            r,
            lambda d: (
                "; ".join(f"{c['key']}: {c['old_value']} -> {c['new_value']} by {c['changed_by']}" for c in d[:3])
                or "no configuration changes"
            ),
            lambda d: {"changes": d},
        )

    def _dependencies(self, service: str, _: Any, ctx: ToolContext) -> CollectionOutcome:
        r = self._call("get_dependency_health", {"service": service}, ctx)
        return self._one(
            E.DEPENDENCIES,
            r,
            lambda d: ", ".join(f"{x['name']}={'ok' if x['healthy'] else 'UNHEALTHY'}" for x in d),
            lambda d: {"dependencies": d},
        )
