"""
CI entry point for the guardrail quality gate.

Fails the build when guardrail effectiveness drops *or* when the false positive
rate climbs — a guardrail that blocks legitimate traffic gets disabled by the
business, which is a worse outcome than the attack it was defending against.

Also compares against the frozen baseline in ``data/baseline.json`` so a slow
slide across commits is caught before it becomes a collapse.

Run: ``python -m app.gate``
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from app.eval_runner import compare_to_baseline, run_eval

REPORT_PATH = Path(__file__).resolve().parents[1] / "eval-report.json"


def main() -> int:
    summary = run_eval(include_redteam=True)
    drift = compare_to_baseline(summary)

    report = {
        "metrics": {
            k: summary[k]
            for k in (
                "n",
                "pass_rate",
                "recall",
                "precision",
                "false_positive_rate",
                "false_negative_rate",
                "block_rate",
                "avg_latency_ms",
            )
        },
        "confusion_matrix": summary["confusion_matrix"],
        "thresholds": summary["thresholds"],
        "gate_passed": summary["gate_passed"],
        "gate_failures": summary["gate_failures"],
        "drift": drift,
        "failures_detail": [d for d in summary["details"] if not d["ok"]],
    }
    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"guardrail suite: {summary['n']} cases (golden + red team)")
    for key, value in report["metrics"].items():
        print(f"  {key:22s} {value}")
    print(f"  confusion              {summary['confusion_matrix']}")

    failed = False

    if not summary["gate_passed"]:
        failed = True
        print("\nGUARDRAIL GATE FAILED")
        for failure in summary["gate_failures"]:
            print(f"  - {failure}")
        for detail in report["failures_detail"]:
            print(
                f"    case {detail['id']}: expected allow={detail['expected_allow']}, "
                f"got allow={detail['allowed']}, rules={detail['rules_triggered']}"
            )

    if drift.get("regressed"):
        failed = True
        print("\nREGRESSION AGAINST BASELINE")
        for regression in drift.get("regressions", []):
            print(f"  - {regression['message']}")

    if failed:
        return 1

    print("\nGUARDRAIL GATE PASSED")
    if not drift.get("has_baseline"):
        print("::notice:: no baseline recorded yet; run POST /eval/baseline to freeze one")
    return 0


if __name__ == "__main__":
    sys.exit(main())
