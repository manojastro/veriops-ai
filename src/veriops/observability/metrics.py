"""Prometheus metrics for the platform itself (not the simulated services)."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

REGISTRY = CollectorRegistry(auto_describe=True)

HTTP_REQUESTS = Counter(
    "veriops_http_requests_total", "HTTP requests", ["method", "route", "status"], registry=REGISTRY
)
HTTP_LATENCY = Histogram(
    "veriops_http_request_duration_seconds",
    "HTTP request latency",
    ["method", "route"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
    registry=REGISTRY,
)
NODE_DURATION = Histogram(
    "veriops_graph_node_duration_seconds",
    "LangGraph node duration",
    ["node", "status"],
    buckets=(0.005, 0.01, 0.05, 0.1, 0.5, 1, 5, 15, 60),
    registry=REGISTRY,
)
NODE_RETRIES = Counter("veriops_graph_node_retries_total", "Node retries", ["node"], registry=REGISTRY)
TOOL_CALLS = Counter("veriops_tool_calls_total", "Tool calls", ["tool", "permission", "outcome"], registry=REGISTRY)
TOOL_LATENCY = Histogram(
    "veriops_tool_call_duration_seconds",
    "Tool call latency",
    ["tool"],
    buckets=(0.005, 0.01, 0.05, 0.1, 0.5, 1, 5, 10),
    registry=REGISTRY,
)
LLM_CALLS = Counter("veriops_llm_calls_total", "LLM calls", ["provider", "model", "task", "outcome"], registry=REGISTRY)
LLM_TOKENS = Counter("veriops_llm_tokens_total", "LLM tokens", ["provider", "model", "kind"], registry=REGISTRY)
LLM_COST = Counter("veriops_llm_cost_usd_total", "Estimated LLM cost (USD)", ["provider", "model"], registry=REGISTRY)
RETRIEVALS = Histogram(
    "veriops_retrieval_duration_seconds", "Knowledge retrieval latency", ["store"], registry=REGISTRY
)
VALIDATIONS = Counter(
    "veriops_validations_total", "Remediation validations", ["validator", "status"], registry=REGISTRY
)
REMEDIATIONS = Counter("veriops_remediations_total", "Executed remediations", ["action", "outcome"], registry=REGISTRY)
INVESTIGATIONS = Counter(
    "veriops_investigations_total", "Investigations by final status", ["status"], registry=REGISTRY
)
INVESTIGATION_DURATION = Histogram(
    "veriops_investigation_duration_seconds",
    "Investigation duration",
    buckets=(1, 5, 15, 30, 60, 120, 300, 900, 3600),
    registry=REGISTRY,
)
AWAITING_APPROVAL = Gauge(
    "veriops_incidents_awaiting_approval", "Incidents waiting for human approval", registry=REGISTRY
)


def record_llm_call(
    provider: str,
    model: str,
    task: str,
    prompt_tokens: int,
    completion_tokens: int,
    cost: float,
    success: bool,
    cached: bool,
) -> None:
    outcome = "cached" if cached else ("success" if success else "error")
    LLM_CALLS.labels(provider, model, task, outcome).inc()
    LLM_TOKENS.labels(provider, model, "prompt").inc(prompt_tokens)
    LLM_TOKENS.labels(provider, model, "completion").inc(completion_tokens)
    LLM_COST.labels(provider, model).inc(cost)


def record_tool_call(tool: str, permission: str, ok: bool, latency_s: float, cached: bool) -> None:
    TOOL_CALLS.labels(tool, permission, "cached" if cached else ("success" if ok else "error")).inc()
    TOOL_LATENCY.labels(tool).observe(latency_s)
