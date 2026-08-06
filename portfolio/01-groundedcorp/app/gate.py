"""
CI entry point for the RAG quality gate.

Exits non-zero when answer or retrieval quality falls below the thresholds in
``eval_harness.GATE``, and writes ``eval-report.json`` for the build artifact so a
reviewer can see *which* question regressed without re-running anything locally.

Run: ``python -m app.gate``
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from app.drift import detect_drift
from app.eval_harness import GATE, run_eval
from app.ingest import ingest_corpus

REPORT_PATH = Path(__file__).resolve().parents[1] / "eval-report.json"


def main() -> int:
    stats = ingest_corpus()
    summary = run_eval()
    drift = detect_drift(window=5)

    report = {
        "corpus": stats,
        "thresholds": GATE,
        "metrics": {
            "n": summary.n,
            "citation_hit_rate": summary.citation_hit_rate,
            "precision_at_k": summary.precision_at_k,
            "recall_at_k": summary.recall_at_k,
            "mrr": summary.mrr,
            "avg_faithfulness": summary.avg_faithfulness,
            "avg_grounding": summary.avg_grounding,
            "refuse_rate": summary.refuse_rate,
            "avg_latency_ms": summary.avg_latency_ms,
            "p95_latency_ms": summary.p95_latency_ms,
            "total_cost_usd": summary.total_cost_usd,
        },
        "gate_passed": summary.gate_passed,
        "gate_failures": summary.gate_failures,
        "drift": {"drifted": drift.drifted, "alerts": drift.alerts},
        "failures_detail": [d for d in summary.details if not d["hit"]],
    }
    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"corpus: {stats['documents']} documents, {stats['chunks']} chunks")
    print(f"golden set: {summary.n} questions")
    for key, value in report["metrics"].items():
        print(f"  {key:22s} {value}")

    if drift.drifted:
        # Drift is a warning, not a build failure: a single noisy run should not
        # block a merge, but it must be visible in the log.
        print("\n::warning:: drift detected against the rolling baseline")
        for alert in drift.alerts:
            print(f"  - {alert['message']}")

    if not summary.gate_passed:
        print("\nQUALITY GATE FAILED")
        for failure in summary.gate_failures:
            print(f"  - {failure}")
        for detail in report["failures_detail"]:
            print(f"    miss: {detail['id']} ({detail.get('refusal_reason') or 'wrong citation'})")
        return 1

    print("\nQUALITY GATE PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
