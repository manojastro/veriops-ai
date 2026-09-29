"""Evidence nodes. The six ``query_*`` / ``inspect_*`` nodes run in parallel (fan-out)."""

from __future__ import annotations

from typing import Any

from veriops.domain.enums import EvidenceKind
from veriops.graph.nodes.base import NodeContext
from veriops.graph.state import InvestigationState

E = EvidenceKind
# Evidence gathered on demand (not in the initial fan-out), in priority order.
ON_DEMAND = [
    E.SLOW_QUERIES,
    E.GIT_DIFF,
    E.DEPENDENCIES,
    E.CONFIG_HISTORY,
    E.CONNECTION_POOL,
    E.DATABASE,
    E.KUBERNETES,
    E.LOGS,
    E.TRACES,
    E.DEPLOYMENTS,
    E.METRICS,
]


def collect_evidence(
    state: InvestigationState, ctx: NodeContext, kinds: list[EvidenceKind]
) -> tuple[dict[str, Any], list]:
    existing = ctx.by_kind(state)
    items, errors = [], []
    fetched: set[EvidenceKind] = set()
    for kind in kinds:
        if kind in fetched:
            continue
        outcome = ctx.deps.evidence_agent.collect(kind, state["service_name"], existing, ctx.tool_context())
        items += outcome.items
        errors += outcome.errors
        for item in outcome.items:
            existing[item.kind] = item
            fetched.add(item.kind)
    return ctx.record_evidence(items), errors


def _evidence_node(kind: EvidenceKind, label: str):  # type: ignore[no-untyped-def]
    def node(state: InvestigationState, ctx: NodeContext) -> dict[str, Any]:
        evidence, errors = collect_evidence(state, ctx, [kind])
        for item in evidence.values():
            ctx.event("evidence", f"{label}: {item.summary}", status="info", evidence_id=item.id, kind=item.kind.value)
        for error in errors:
            ctx.event("evidence_error", f"{label} unavailable", error, status="warning")
        return {"evidence": evidence}

    node.__name__ = label
    return node


query_metrics = _evidence_node(E.METRICS, "Metrics retrieved")
query_logs = _evidence_node(E.LOGS, "Logs analyzed")
query_traces = _evidence_node(E.TRACES, "Traces inspected")
inspect_kubernetes = _evidence_node(E.KUBERNETES, "Kubernetes inspected")
inspect_database = _evidence_node(E.DATABASE, "Database inspected")


def inspect_recent_deployments(state: InvestigationState, ctx: NodeContext) -> dict[str, Any]:
    evidence, errors = collect_evidence(state, ctx, [E.DEPLOYMENTS])
    for item in evidence.values():
        signals = ctx.signals(state, evidence)
        deploy = signals.recent_deployment
        if deploy:
            ctx.event(
                "deployment_identified",
                f"Deployment #{deploy.id} identified ({deploy.minutes_ago:.0f} min ago)",
                f"{deploy.description} — by {deploy.author}, commit {deploy.commit_sha}",
                status="warning",
                evidence_id=item.id,
                deployment_id=deploy.id,
            )
        else:
            ctx.event("evidence", f"No deployment in the last 6h ({item.summary})", evidence_id=item.id)
    for error in errors:
        ctx.event("evidence_error", "Deployment history unavailable", error, status="warning")
    return {"evidence": evidence}


def inspect_git_changes(state: InvestigationState, ctx: NodeContext) -> dict[str, Any]:
    signals = ctx.signals(state)
    if not signals.recent_deployment:
        ctx.event("evidence", "Git inspection skipped: no recent deployment")
        return {}
    evidence, errors = collect_evidence(state, ctx, [E.GIT_COMMIT])
    for item in evidence.values():
        ctx.event("evidence", f"Commit inspected: {item.summary}", evidence_id=item.id)
    for error in errors:
        ctx.event("evidence_error", "Git commit unavailable", error, status="warning")
    return {"evidence": evidence}


def gather_additional_evidence(state: InvestigationState, ctx: NodeContext) -> dict[str, Any]:
    """Collect evidence the current hypothesis needs (or, after 'more investigation', everything missing)."""
    pending = list(state.get("pending_evidence") or [])
    collected = set(ctx.by_kind(state))
    if not pending:
        pending = [k for k in ON_DEMAND if k not in collected]
    kinds = [k for k in pending if k not in collected] or pending
    iteration = state.get("investigation_iterations", 0) + 1
    evidence, errors = collect_evidence(state, ctx, kinds)
    summaries = "; ".join(f"{i.kind.value}: {i.summary}" for i in evidence.values())
    ctx.event(
        "additional_evidence",
        f"Additional evidence collected (iteration {iteration}): {', '.join(k.value for k in kinds)}",
        summaries[:900],
        status="info",
        kinds=[k.value for k in kinds],
        evidence_ids=list(evidence),
    )
    for error in errors:
        ctx.event("evidence_error", "Evidence collection failed", error, status="warning")
    return {"evidence": evidence, "investigation_iterations": iteration, "pending_evidence": []}
