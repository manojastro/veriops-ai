"""Knowledge retrieval nodes (RAG through the retrieve_* tools)."""

from __future__ import annotations

from typing import Any

from veriops.agents.signals import IncidentSignals
from veriops.domain.models import DocumentCitation
from veriops.graph.nodes.base import NodeContext
from veriops.graph.state import InvestigationState


def build_query(signals: IncidentSignals) -> str:
    parts = [signals.service]
    if signals.latency_breach:
        parts.append("high latency")
    if signals.error_breach:
        parts.append("error rate")
    if signals.db_pool_utilization_pct >= 90:
        parts.append("database connection pool exhaustion slow query")
    if signals.memory_pct >= 85 or signals.oom_killed:
        parts.append("memory leak OOMKilled")
    if signals.cpu_pct >= 85:
        parts.append("CPU saturation scaling")
    if "upstream_timeout" in signals.log_keywords:
        parts.append("gateway timeout dependency failover")
    if "cache_miss" in signals.log_keywords:
        parts.append("cache configuration change")
    if "exception" in signals.log_keywords:
        parts.append("exceptions after deployment")
    if signals.recent_deployment:
        parts.append("deployment rollback")
    return " ".join(parts)


def search(ctx: NodeContext, tool: str, query: str, service: str, top_k: int = 4) -> list[DocumentCitation]:
    result = ctx.deps.tools.invoke(tool, {"query": query[:500], "service": service, "top_k": top_k}, ctx.tool_context())
    if not result.ok:
        ctx.event("retrieval_error", f"{tool} failed", result.error or "", status="warning")
        return []
    return [DocumentCitation(**c) for c in result.data]


def _report_flags(ctx: NodeContext, citations: list[DocumentCitation]) -> None:
    for c in citations:
        if c.flagged_injection:
            ctx.event(
                "prompt_injection_neutralised",
                f"Prompt-injection content neutralised in '{c.title}'",
                "Instruction-like lines were removed before the document reached any model; the document is "
                "treated as untrusted data.",
                status="warning",
                document_id=c.document_id,
            )
            ctx.audit(
                "knowledge.injection_neutralised",
                decision="SANITIZED",
                reason="instruction-like content",
                input={"document_id": c.document_id, "chunk_id": c.chunk_id},
            )


def retrieve_runbooks(state: InvestigationState, ctx: NodeContext) -> dict[str, Any]:
    signals = ctx.signals(state)
    query = build_query(signals)
    citations = search(ctx, "retrieve_runbook", query, state["service_name"], top_k=4)
    _report_flags(ctx, citations)
    if citations:
        ctx.event(
            "knowledge",
            f"Retrieved {len(citations)} runbooks/docs (top: {citations[0].title})",
            " | ".join(
                f"{c.title} [vec {c.vector_score:.2f} kw {c.keyword_score:.2f} rerank {c.rerank_score:.2f}]"
                for c in citations
            ),
            query=query,
        )
    return {"retrieved_runbooks": citations}


def retrieve_similar_incidents(state: InvestigationState, ctx: NodeContext) -> dict[str, Any]:
    signals = ctx.signals(state)
    citations = search(ctx, "retrieve_similar_incidents", build_query(signals), state["service_name"], top_k=3)
    _report_flags(ctx, citations)
    if citations:
        ctx.event(
            "knowledge",
            f"Similar incident found: {citations[0].title}",
            " | ".join(f"{c.title} [rerank {c.rerank_score:.2f}]" for c in citations),
        )
    return {"similar_incidents": citations}
