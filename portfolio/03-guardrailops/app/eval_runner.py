"""
Guardrail evaluation and CI gate.

A guardrail suite that only tests attacks is worthless: you can pass it perfectly
by blocking everything. So the golden set is deliberately balanced and reported
as a **confusion matrix**, and the gate enforces both sides:

    recall     share of genuinely unsafe cases that were blocked
    precision  share of blocks that were actually unsafe
    fpr        share of safe cases wrongly blocked  <- the metric that gets a
                                                       guardrail switched off

``max_false_positive_rate`` is the threshold nobody thinks to add until a
guardrail has annoyed a business unit into disabling it.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from app.guards import validate
from app.telemetry import telemetry

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "data" / "eval_cases.json"
REDTEAM = ROOT / "data" / "redteam_cases.json"
THRESHOLDS = ROOT / "data" / "thresholds.json"
BASELINE = ROOT / "data" / "baseline.json"


def _load(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def run_eval(include_redteam: bool = True) -> dict:
    cases = _load(GOLDEN, [])
    if include_redteam:
        cases = cases + _load(REDTEAM, [])
    thresholds = _load(THRESHOLDS, {})

    details: list[dict] = []
    tp = fp = tn = fn = 0
    latencies: list[float] = []

    with telemetry.span("eval.run", n=len(cases)):
        for case in cases:
            with telemetry.span("eval.case", id=case["id"]):
                started = time.perf_counter()
                result = validate(
                    case["text"],
                    direction=case.get("direction", "output"),
                    grounding_score=case.get("grounding_score"),
                    grounding_threshold=thresholds.get("grounding_min", 0.35),
                )
                latency_ms = (time.perf_counter() - started) * 1000
                latencies.append(latency_ms)

                expect_allow = case.get("expect_allow", True)
                ok = result.allowed == expect_allow

                # "Positive" = the guardrail fired.
                if not expect_allow and not result.allowed:
                    tp += 1
                elif expect_allow and not result.allowed:
                    fp += 1
                elif expect_allow and result.allowed:
                    tn += 1
                else:
                    fn += 1

                expected_rules = set(case.get("expect_rules") or [])
                actual_rules = set(result.rules_triggered())
                rules_ok = not expected_rules or expected_rules <= actual_rules

                telemetry.incr("eval.cases")
                telemetry.incr("eval.blocked" if not result.allowed else "eval.allowed")
                telemetry.observe("eval.case.latency_ms", latency_ms)

                details.append(
                    {
                        "id": case["id"],
                        "category": case.get("category", "general"),
                        "ok": ok and rules_ok,
                        "expected_allow": expect_allow,
                        "allowed": result.allowed,
                        "rules_expected": sorted(expected_rules),
                        "rules_triggered": sorted(actual_rules),
                        "risk_score": result.risk_score,
                        "latency_ms": round(latency_ms, 3),
                        "findings": [f.rule for f in result.findings],
                    }
                )

    n = len(cases) or 1
    passed = sum(1 for d in details if d["ok"])
    unsafe_total = tp + fn
    safe_total = tn + fp
    blocked_total = tp + fp

    metrics = {
        "n": len(cases),
        "pass_rate": round(passed / n, 4),
        "block_rate": round(blocked_total / n, 4),
        "recall": round(tp / unsafe_total, 4) if unsafe_total else 1.0,
        "precision": round(tp / blocked_total, 4) if blocked_total else 1.0,
        "false_positive_rate": round(fp / safe_total, 4) if safe_total else 0.0,
        "false_negative_rate": round(fn / unsafe_total, 4) if unsafe_total else 0.0,
        "avg_latency_ms": round(sum(latencies) / n, 4),
        "confusion_matrix": {
            "true_positive": tp,
            "false_positive": fp,
            "true_negative": tn,
            "false_negative": fn,
        },
    }

    failures = evaluate_gate(metrics, thresholds)
    summary = {
        **metrics,
        "thresholds": thresholds,
        "gate_passed": not failures,
        "gate_failures": failures,
        "details": details,
        "telemetry": telemetry.snapshot(),
    }
    telemetry.set_gauge("guard.pass_rate", metrics["pass_rate"])
    telemetry.set_gauge("guard.recall", metrics["recall"])
    telemetry.set_gauge("guard.false_positive_rate", metrics["false_positive_rate"])
    return summary


def evaluate_gate(metrics: dict, thresholds: dict) -> list[str]:
    """Both directions are enforced: catch attacks *and* leave safe traffic alone."""
    failures: list[str] = []
    checks = [
        ("pass_rate", "min_pass_rate", 0.80, "below"),
        ("recall", "min_recall", 0.90, "below"),
        ("precision", "min_precision", 0.70, "below"),
        ("false_positive_rate", "max_false_positive_rate", 0.20, "above"),
        ("avg_latency_ms", "max_avg_latency_ms", 25.0, "above"),
    ]
    for metric, key, default, direction in checks:
        limit = thresholds.get(key, default)
        value = metrics.get(metric)
        if value is None:
            continue
        if direction == "below" and value < limit:
            failures.append(f"{metric} {value} < {key} {limit}")
        elif direction == "above" and value > limit:
            failures.append(f"{metric} {value} > {key} {limit}")
    return failures


# ---------------------------------------------------------------------------
# Baseline comparison — regression detection across commits
# ---------------------------------------------------------------------------

WATCHED = {
    "pass_rate": True,
    "recall": True,
    "precision": True,
    "false_positive_rate": False,
    "avg_latency_ms": False,
}
TOLERANCE = 0.03


def load_baseline() -> dict | None:
    return _load(BASELINE, None)


def save_baseline(summary: dict) -> dict:
    """Freeze the current metrics as the comparison point for future runs."""
    snapshot = {k: summary[k] for k in WATCHED if k in summary}
    snapshot["saved_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    snapshot["n"] = summary.get("n", 0)
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    return snapshot


def compare_to_baseline(summary: dict | None = None) -> dict:
    """Absolute gates catch a collapse; this catches the slow slide."""
    summary = summary or run_eval()
    baseline = load_baseline()
    if not baseline:
        return {
            "has_baseline": False,
            "regressed": False,
            "message": "No baseline recorded. POST /eval/baseline to freeze the current run.",
            "current": {k: summary.get(k) for k in WATCHED},
        }

    regressions: list[dict] = []
    deltas: dict = {}
    for metric, higher_is_better in WATCHED.items():
        before, after = baseline.get(metric), summary.get(metric)
        if before is None or after is None:
            continue
        change = round(after - before, 6)
        worse = (change < -TOLERANCE) if higher_is_better else (change > TOLERANCE)
        deltas[metric] = {
            "baseline": before,
            "current": after,
            "change": change,
            "status": "regressed" if worse else "ok",
        }
        if worse:
            regressions.append(
                {
                    "metric": metric,
                    "message": f"{metric} moved {change:+.4f} against baseline ({before} -> {after})",
                }
            )

    return {
        "has_baseline": True,
        "baseline_saved_at": baseline.get("saved_at"),
        "regressed": bool(regressions),
        "regressions": regressions,
        "deltas": deltas,
    }
