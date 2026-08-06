"""
OpenTelemetry-shaped, dependency-free observability for the portfolio platform.

Design goals
------------
- **Zero dependencies.** No OTEL SDK, no collector, no numpy. Runs in CI and offline.
- **W3C-compatible context.** Trace/span ids follow the `traceparent` wire format so a
  real OTLP exporter can be dropped in later without changing call sites.
- **Cross-service correlation.** `GroundedCorp -> shared guardrails -> GuardRailOps`
  all emit spans under one trace id, propagated over HTTP via `traceparent`.
- **Prometheus exposition.** Hand-rolled text format, scrapeable as-is.

Usage
-----
    from shared.observability import get_tracer

    tracer = get_tracer("groundedcorp")
    with tracer.span("rag.retrieve", top_k=4) as span:
        span.set_attribute("hits", len(hits))
"""

from __future__ import annotations

import os
import random
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import Lock
from typing import Iterator

# ---------------------------------------------------------------------------
# Trace context (W3C traceparent: 00-<32 hex trace>-<16 hex span>-<2 hex flags>)
# ---------------------------------------------------------------------------

_TRACEPARENT_RE = re.compile(
    r"^00-(?P<trace>[0-9a-f]{32})-(?P<span>[0-9a-f]{16})-(?P<flags>[0-9a-f]{2})$"
)


@dataclass(frozen=True)
class TraceContext:
    trace_id: str
    span_id: str
    sampled: bool = True

    def to_traceparent(self) -> str:
        return f"00-{self.trace_id}-{self.span_id}-{'01' if self.sampled else '00'}"

    @staticmethod
    def parse(traceparent: str | None) -> "TraceContext | None":
        if not traceparent:
            return None
        m = _TRACEPARENT_RE.match(traceparent.strip().lower())
        if not m:
            return None
        return TraceContext(
            trace_id=m.group("trace"),
            span_id=m.group("span"),
            sampled=bool(int(m.group("flags"), 16) & 0x01),
        )


_current_context: ContextVar[TraceContext | None] = ContextVar(
    "otel_current_context", default=None
)


def _rand_hex(n_bytes: int) -> str:
    return "%0*x" % (n_bytes * 2, random.getrandbits(n_bytes * 8))


def new_trace_id() -> str:
    return _rand_hex(16)


def new_span_id() -> str:
    return _rand_hex(8)


def current_context() -> TraceContext | None:
    """Trace context for the in-flight request, if any."""
    return _current_context.get()


def current_trace_id() -> str | None:
    ctx = _current_context.get()
    return ctx.trace_id if ctx else None


@contextmanager
def use_context(ctx: TraceContext | None) -> Iterator[TraceContext | None]:
    """Bind an incoming (usually remote) trace context for the duration of a block."""
    token = _current_context.set(ctx)
    try:
        yield ctx
    finally:
        _current_context.reset(token)


# ---------------------------------------------------------------------------
# Spans
# ---------------------------------------------------------------------------


@dataclass
class Span:
    name: str
    trace_id: str
    span_id: str
    parent_span_id: str | None
    service: str
    start_ms: float
    end_ms: float | None = None
    attributes: dict = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)
    status: str = "ok"

    @property
    def duration_ms(self) -> float:
        return round((self.end_ms or self.start_ms) - self.start_ms, 3)

    def set_attribute(self, key: str, value) -> None:
        self.attributes[key] = value

    def add_event(self, name: str, **attrs) -> None:
        self.events.append({"name": name, "ts_ms": time.time() * 1000, **attrs})

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "service": self.service,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "attributes": self.attributes,
            "events": self.events,
        }


# ---------------------------------------------------------------------------
# Tracer / meter
# ---------------------------------------------------------------------------

_MAX_SPANS = int(os.getenv("OTEL_MAX_SPANS", "500"))
_METRIC_SAFE = re.compile(r"[^a-zA-Z0-9_]")


class Telemetry:
    """In-process tracer + meter with Prometheus exposition.

    Thread-safe. Bounded span buffer so a long-running process cannot leak memory.
    """

    def __init__(self, service: str = "portfolio", metric_prefix: str = "gro") -> None:
        self.service = service
        self.metric_prefix = metric_prefix
        self._lock = Lock()
        self.spans: list[Span] = []
        self.counters: dict[str, float] = {}
        self.histograms: dict[str, list[float]] = {}
        self.gauges: dict[str, float] = {}

    # -- metrics ------------------------------------------------------------

    def incr(self, name: str, value: float = 1.0) -> None:
        with self._lock:
            self.counters[name] = self.counters.get(name, 0.0) + value

    def observe(self, name: str, value: float) -> None:
        with self._lock:
            bucket = self.histograms.setdefault(name, [])
            bucket.append(value)
            # Keep histograms bounded; retain the most recent window.
            if len(bucket) > 2000:
                del bucket[: len(bucket) - 2000]

    def set_gauge(self, name: str, value: float) -> None:
        with self._lock:
            self.gauges[name] = value

    # -- tracing ------------------------------------------------------------

    @contextmanager
    def span(self, name: str, **attrs) -> Iterator[Span]:
        parent = _current_context.get()
        trace_id = parent.trace_id if parent else new_trace_id()
        span_id = new_span_id()
        s = Span(
            name=name,
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=parent.span_id if parent else None,
            service=self.service,
            start_ms=time.perf_counter() * 1000,
            attributes=dict(attrs),
        )
        token = _current_context.set(
            TraceContext(trace_id=trace_id, span_id=span_id, sampled=True)
        )
        try:
            yield s
        except Exception as exc:  # noqa: BLE001 - re-raised below
            s.status = "error"
            s.attributes["error.type"] = type(exc).__name__
            s.attributes["error.message"] = str(exc)
            self.incr(f"span.{name}.errors")
            raise
        finally:
            _current_context.reset(token)
            s.end_ms = time.perf_counter() * 1000
            self.observe(f"span.{name}.latency_ms", s.duration_ms)
            self.incr(f"span.{name}.count")
            with self._lock:
                self.spans.append(s)
                if len(self.spans) > _MAX_SPANS:
                    del self.spans[: len(self.spans) - _MAX_SPANS]

    # -- export -------------------------------------------------------------

    @staticmethod
    def _quantile(ordered: list[float], q: float) -> float:
        if not ordered:
            return 0.0
        idx = min(max(int(len(ordered) * q) - 1, 0), len(ordered) - 1)
        return ordered[idx]

    def snapshot(self) -> dict:
        with self._lock:
            hist = {}
            for k, v in self.histograms.items():
                ordered = sorted(v)
                hist[k] = {
                    "count": len(v),
                    "avg": round(sum(v) / len(v), 4) if v else 0,
                    "p50": round(self._quantile(ordered, 0.50), 4),
                    "p95": round(self._quantile(ordered, 0.95), 4),
                    "p99": round(self._quantile(ordered, 0.99), 4),
                }
            return {
                "service": self.service,
                "counters": dict(self.counters),
                "gauges": dict(self.gauges),
                "histograms": hist,
                "recent_spans": [s.to_dict() for s in self.spans[-20:]],
            }

    def trace(self, trace_id: str) -> list[dict]:
        """All spans for one trace id, oldest first — the AgentOps replay view."""
        with self._lock:
            return [s.to_dict() for s in self.spans if s.trace_id == trace_id]

    def _metric_name(self, name: str) -> str:
        return f"{self.metric_prefix}_" + _METRIC_SAFE.sub("_", name)

    def prometheus_text(self) -> str:
        """Prometheus text exposition format v0.0.4."""
        lines: list[str] = []
        with self._lock:
            for name, value in sorted(self.counters.items()):
                metric = self._metric_name(name)
                lines.append(f"# TYPE {metric} counter")
                lines.append(f"{metric} {value}")
            for name, value in sorted(self.gauges.items()):
                metric = self._metric_name(name)
                lines.append(f"# TYPE {metric} gauge")
                lines.append(f"{metric} {value}")
            for name, values in sorted(self.histograms.items()):
                metric = self._metric_name(name)
                ordered = sorted(values)
                lines.append(f"# TYPE {metric} summary")
                lines.append(f"{metric}_count {len(values)}")
                lines.append(f"{metric}_sum {sum(values) if values else 0.0}")
                if values:
                    for q in (0.5, 0.95, 0.99):
                        lines.append(
                            f'{metric}{{quantile="{q}"}} {self._quantile(ordered, q)}'
                        )
        lines.append("")
        return "\n".join(lines)

    def reset(self) -> None:
        with self._lock:
            self.spans.clear()
            self.counters.clear()
            self.histograms.clear()
            self.gauges.clear()


_tracers: dict[str, Telemetry] = {}
_tracers_lock = Lock()


def get_tracer(service: str = "portfolio", metric_prefix: str = "gro") -> Telemetry:
    """Process-wide singleton per service name."""
    with _tracers_lock:
        if service not in _tracers:
            _tracers[service] = Telemetry(service=service, metric_prefix=metric_prefix)
        return _tracers[service]
