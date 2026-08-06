"""
Drift detection over eval history.

A model or corpus change rarely breaks a system outright — it degrades it. The
citation hit rate slips four points, p95 latency creeps up, cost per query
doubles after a chunking change. None of that trips an absolute threshold, so
none of it pages anyone, and six weeks later the assistant is quietly bad.

This compares the latest run against a rolling baseline of prior runs and alerts
on *relative* movement. Metrics are directional: for latency and cost an increase
is the regression; for quality metrics a decrease is.
"""

from __future__ import annotations

from app.eval_harness import list_eval_history
from app.models import DriftReport

# metric -> (higher_is_better, relative tolerance, absolute floor for noise)
WATCHED_METRICS = {
    "citation_hit_rate": (True, 0.10, 0.02),
    "avg_faithfulness": (True, 0.15, 0.02),
    "mrr": (True, 0.10, 0.02),
    "avg_grounding": (True, 0.15, 0.02),
    "p95_latency_ms": (False, 0.50, 25.0),
    "avg_cost_usd": (False, 0.50, 0.0000005),
    "refuse_rate": (False, 0.30, 0.05),
}

BASELINE_WINDOW = 5


def detect_drift(window: int = BASELINE_WINDOW) -> DriftReport:
    history = list_eval_history(limit=window + 1)
    if len(history) < 2:
        return DriftReport(
            drifted=False,
            alerts=[{"metric": "n/a", "message": "Need at least 2 eval runs to compare."}],
            history=history,
        )

    latest, *baseline_runs = history  # history is newest-first
    baseline_runs = baseline_runs[:window]

    alerts: list[dict] = []
    deltas: dict = {}

    for metric, (higher_is_better, tolerance, noise_floor) in WATCHED_METRICS.items():
        values = [r[metric] for r in baseline_runs if r.get(metric) is not None]
        if not values:
            continue
        baseline = sum(values) / len(values)
        current = latest.get(metric)
        if current is None:
            continue

        change = current - baseline
        if abs(change) < noise_floor:
            deltas[metric] = {
                "baseline": round(baseline, 6),
                "current": round(current, 6),
                "change": round(change, 6),
                "status": "stable",
            }
            continue

        relative = change / baseline if baseline else (1.0 if change else 0.0)
        regressed = (change < 0) if higher_is_better else (change > 0)
        breached = regressed and abs(relative) > tolerance

        deltas[metric] = {
            "baseline": round(baseline, 6),
            "current": round(current, 6),
            "change": round(change, 6),
            "relative_change": round(relative, 4),
            "status": "regressed" if breached else ("improved" if not regressed else "within_tolerance"),
        }

        if breached:
            direction = "dropped" if higher_is_better else "increased"
            alerts.append(
                {
                    "metric": metric,
                    "severity": "high" if abs(relative) > tolerance * 2 else "medium",
                    "message": (
                        f"{metric} {direction} {abs(relative) * 100:.1f}% vs "
                        f"{len(values)}-run baseline "
                        f"({baseline:.4f} -> {current:.4f})"
                    ),
                }
            )

    return DriftReport(
        baseline_run_id=baseline_runs[-1]["id"] if baseline_runs else None,
        latest_run_id=latest["id"],
        drifted=bool(alerts),
        alerts=alerts,
        deltas=deltas,
        history=history,
    )
