"""Re-export shared portfolio guardrails (single source of truth)."""

from __future__ import annotations

import sys
from pathlib import Path

_PORTFOLIO = Path(__file__).resolve().parents[2]
if str(_PORTFOLIO) not in sys.path:
    sys.path.insert(0, str(_PORTFOLIO))

from shared.guardrails import GuardFinding, GuardResult, validate  # noqa: E402

__all__ = ["GuardFinding", "GuardResult", "validate"]
