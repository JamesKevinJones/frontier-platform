"""ASGI middleware that gives every FastAPI service the same trace + metrics surface.

Installing it does three things:

1. Adopts an inbound ``traceparent`` header (or mints a new trace) so a request that
   fans out across GroundedCorp -> GuardRailOps shares one trace id.
2. Records an HTTP server span with route, status and latency, plus RED metrics
   (Rate, Errors, Duration) that Prometheus can scrape.
3. Echoes ``traceparent`` and ``X-Trace-Id`` back on the response so the browser
   console can deep-link straight into ``/observability/trace/{trace_id}``.
"""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .core import (
    TraceContext,
    Telemetry,
    new_span_id,
    new_trace_id,
    use_context,
)


class TraceMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, telemetry: Telemetry) -> None:
        super().__init__(app)
        self.telemetry = telemetry

    async def dispatch(self, request: Request, call_next):
        incoming = TraceContext.parse(request.headers.get("traceparent"))
        ctx = incoming or TraceContext(
            trace_id=new_trace_id(), span_id=new_span_id(), sampled=True
        )

        route = request.url.path
        started = time.perf_counter()
        status_code = 500
        with use_context(ctx):
            try:
                with self.telemetry.span(
                    "http.server",
                    **{
                        "http.method": request.method,
                        "http.route": route,
                        "service.name": self.telemetry.service,
                    },
                ) as span:
                    response: Response = await call_next(request)
                    status_code = response.status_code
                    span.set_attribute("http.status_code", status_code)
                    if status_code >= 500:
                        span.status = "error"
            finally:
                elapsed_ms = (time.perf_counter() - started) * 1000
                self.telemetry.incr("http.requests")
                self.telemetry.observe("http.server.duration_ms", elapsed_ms)
                if status_code >= 500:
                    self.telemetry.incr("http.errors")

        response.headers["traceparent"] = ctx.to_traceparent()
        response.headers["X-Trace-Id"] = ctx.trace_id
        return response


def install_observability(app, telemetry: Telemetry) -> None:
    """Attach trace middleware plus the standard ``/observability/*`` endpoints.

    Every service exposes the same three routes, so one console page can render
    metrics for all of them without per-service special-casing.
    """
    app.add_middleware(TraceMiddleware, telemetry=telemetry)

    @app.get("/observability/metrics", tags=["observability"])
    def observability_metrics():
        return telemetry.snapshot()

    @app.get("/observability/metrics/prometheus", tags=["observability"])
    def observability_prometheus():
        return Response(
            content=telemetry.prometheus_text(),
            media_type="text/plain; version=0.0.4",
        )

    @app.get("/observability/trace/{trace_id}", tags=["observability"])
    def observability_trace(trace_id: str):
        return {"trace_id": trace_id, "spans": telemetry.trace(trace_id)}
