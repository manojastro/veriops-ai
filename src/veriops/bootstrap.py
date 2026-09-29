"""Composition root: builds the object graph for the API, the worker, the CLI demo and evaluations."""

from __future__ import annotations

from dataclasses import dataclass

from veriops.domain.models import Alert
from veriops.graph.deps import GraphDeps
from veriops.graph.recorder import IncidentRecorder, InMemoryRecorder
from veriops.llm.gateway import LLMGateway, build_gateway
from veriops.llm.providers.base import LLMProviderClient
from veriops.rag.embeddings import build_embedder
from veriops.rag.ingest import DocumentIngestor
from veriops.rag.retriever import HybridRetriever
from veriops.rag.store import InMemoryStore, KnowledgeStore
from veriops.shared.config import Settings, TelemetryBackendKind, get_settings
from veriops.simulator.world import SimulatorWorld
from veriops.tools.backends import HttpSimulatorBackend, InfrastructureBackend, InProcessSimulatorBackend
from veriops.tools.catalog import build_tool_registry
from veriops.tools.registry import ToolRegistry


@dataclass
class Platform:
    settings: Settings
    backend: InfrastructureBackend
    store: KnowledgeStore
    retriever: HybridRetriever
    ingestor: DocumentIngestor
    tools: ToolRegistry
    llm: LLMGateway
    recorder: IncidentRecorder

    def graph_deps(self) -> GraphDeps:
        return GraphDeps(settings=self.settings, tools=self.tools, llm=self.llm, recorder=self.recorder)


def build_platform(
    settings: Settings | None = None,
    *,
    backend: InfrastructureBackend | None = None,
    world: SimulatorWorld | None = None,
    store: KnowledgeStore | None = None,
    recorder: IncidentRecorder | None = None,
    provider: LLMProviderClient | None = None,
    sanitize_documents: bool = True,
) -> Platform:
    settings = settings or get_settings()
    if backend is None:
        if settings.telemetry_backend == TelemetryBackendKind.HTTP and world is None:
            backend = HttpSimulatorBackend(settings.simulator_url, timeout_s=settings.tool_timeout_seconds)
        else:
            backend = InProcessSimulatorBackend(world or SimulatorWorld())
    embedder = build_embedder(settings)
    store = store or InMemoryStore()
    retriever = HybridRetriever(store=store, embedder=embedder, sanitize_documents=sanitize_documents)
    tools = build_tool_registry(
        backend, retriever, max_retries=settings.max_tool_retries, timeout_s=settings.tool_timeout_seconds
    )
    return Platform(
        settings=settings,
        backend=backend,
        store=store,
        retriever=retriever,
        ingestor=DocumentIngestor(store, embedder),
        tools=tools,
        llm=build_gateway(settings, provider),
        recorder=recorder or InMemoryRecorder(),
    )


def alert_from_activation(activation: dict[str, object]) -> Alert:
    metrics = activation["metrics"]
    assert isinstance(metrics, dict)
    return Alert(
        service=str(activation["service"]),
        environment=str(activation["environment"]),
        title=str(activation["title"]),
        p95_latency_ms=metrics["p95_latency_ms"],
        error_rate_pct=metrics["error_rate_pct"],
        cpu_pct=metrics["cpu_pct"],
        memory_pct=metrics["memory_pct"],
        db_pool_utilization_pct=metrics["db_pool_utilization_pct"],
        labels={"severity": str(activation.get("severity", "SEV2")), "scenario": str(activation.get("scenario", ""))},
    )
