"""OpenTelemetry tracing setup and helpers.

Tracing is a no-op until ``configure_tracing`` is called with ``OTEL_ENABLED=true``; the helpers are
always safe to call.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

logger = logging.getLogger(__name__)
_tracer = trace.get_tracer("veriops")
_configured = False


def configure_tracing(service_name: str, otlp_endpoint: str) -> None:
    global _configured, _tracer
    if _configured:
        return
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{otlp_endpoint.rstrip('/')}/v1/traces")))
    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer("veriops")
    _configured = True
    logger.info("OpenTelemetry tracing enabled -> %s", otlp_endpoint)


@contextmanager
def span(name: str, attributes: dict[str, Any] | None = None) -> Iterator[trace.Span]:
    with _tracer.start_as_current_span(name) as current:
        for key, value in (attributes or {}).items():
            if value is not None:
                current.set_attribute(key, value if isinstance(value, str | int | float | bool) else str(value))
        try:
            yield current
        except Exception as exc:
            current.record_exception(exc)
            current.set_status(trace.Status(trace.StatusCode.ERROR, str(exc)))
            raise


def current_trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx and ctx.trace_id else None
