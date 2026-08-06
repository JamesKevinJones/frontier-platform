"""Telemetry — re-exported from ``shared.observability``.

Originally implemented here; promoted to the shared layer once GroundedCorp and
AgentForge needed the same spans and the same Prometheus exposition. One tracer
implementation means one metric naming convention across all three services, which
is what makes a single console page possible.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PORTFOLIO = Path(__file__).resolve().parents[2]
if str(_PORTFOLIO) not in sys.path:
    sys.path.insert(0, str(_PORTFOLIO))

from shared.observability import (  # noqa: E402
    Span,
    Telemetry,
    TraceContext,
    current_trace_id,
    get_tracer,
)

telemetry = get_tracer("guardrailops")

__all__ = [
    "Span",
    "Telemetry",
    "TraceContext",
    "current_trace_id",
    "get_tracer",
    "telemetry",
]
