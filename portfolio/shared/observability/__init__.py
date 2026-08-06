"""Dependency-free, OpenTelemetry-shaped tracing and metrics for the platform."""

from .core import (
    Span,
    Telemetry,
    TraceContext,
    current_context,
    current_trace_id,
    get_tracer,
    new_span_id,
    new_trace_id,
    use_context,
)
from .middleware import TraceMiddleware, install_observability

__all__ = [
    "Span",
    "Telemetry",
    "TraceContext",
    "TraceMiddleware",
    "current_context",
    "current_trace_id",
    "get_tracer",
    "install_observability",
    "new_span_id",
    "new_trace_id",
    "use_context",
]
