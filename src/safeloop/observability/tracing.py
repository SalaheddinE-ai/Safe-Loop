"""Optional OpenTelemetry tracing. No-op unless `opentelemetry` is installed AND a tracer is
configured (SAFELOOP_OTLP_ENDPOINT or configure_tracing(exporter=...)). Spans are only created for
stages that actually execute."""
from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

_tracer: Any = None


def configure_tracing(exporter: Any | None = None, service_name: str = "safeloop") -> bool:
    global _tracer
    try:
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    except ImportError:
        return False
    if exporter is None:
        endpoint = os.environ.get("SAFELOOP_OTLP_ENDPOINT")
        if not endpoint:
            return False
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        exporter = OTLPSpanExporter(endpoint=endpoint)
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    _tracer = provider.get_tracer("safeloop")
    return True


def reset_tracing() -> None:
    global _tracer
    _tracer = None


@contextmanager
def span(name: str, **attrs: Any) -> Iterator[None]:
    if _tracer is None:
        yield
        return
    with _tracer.start_as_current_span(name) as s:
        for k, v in attrs.items():
            s.set_attribute(k, v if isinstance(v, str | int | float | bool) else str(v))
        yield
