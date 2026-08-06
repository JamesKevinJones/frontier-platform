"""
Append-only run store — the AgentOps persistence layer.

Two artefacts per run:

    <id>.jsonl   append-only event log; every attempt is retained, so a run that
                 was re-executed shows its full history rather than the last word
    <id>.json    latest snapshot plus checkpoints, for cheap GET and replay

Append-only matters for agents specifically: the interesting failures are the ones
where the agent looped, revised and *then* looked fine. An overwritten record hides
exactly the behaviour you need to debug.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.models import RunResult

ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = ROOT / "data" / "runs"


def _ensure() -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)


def persist_run(result: RunResult, checkpoints: list[dict] | None = None) -> Path:
    _ensure()
    log_path = RUNS_DIR / f"{result.ticket_id}.jsonl"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(result.model_dump_json() + "\n")

    snapshot = result.model_dump()
    snapshot["checkpoints"] = checkpoints or []
    (RUNS_DIR / f"{result.ticket_id}.json").write_text(
        json.dumps(snapshot, indent=2), encoding="utf-8"
    )
    return log_path


def list_runs(limit: int = 50) -> list[dict]:
    _ensure()
    files = sorted(RUNS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    out: list[dict] = []
    for path in files[:limit]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        out.append(
            {
                "ticket_id": data.get("ticket_id"),
                "category": data.get("category"),
                "severity": data.get("severity"),
                "status": data.get("status"),
                "total_cost_units": data.get("total_cost_units"),
                "total_latency_ms": data.get("total_latency_ms"),
                "budget_exceeded": data.get("budget_exceeded", False),
                "loops": data.get("loops", 0),
                "confidence": data.get("confidence", 0.0),
                "created_at": data.get("created_at"),
                "trace_id": data.get("trace_id"),
                "needs_human": (data.get("human_review") or {}).get("required", False),
                "path": data.get("path", []),
            }
        )
    return out


def get_run(ticket_id: str) -> dict | None:
    _ensure()
    path = RUNS_DIR / f"{ticket_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def get_run_history(ticket_id: str) -> list[dict]:
    """Every persisted attempt for a ticket, oldest first."""
    _ensure()
    path = RUNS_DIR / f"{ticket_id}.jsonl"
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def aggregate_stats(limit: int = 200) -> dict:
    """Fleet-level view: what does this agent cost and how often does it need a human?"""
    runs = list_runs(limit=limit)
    if not runs:
        return {
            "runs": 0,
            "resolved_rate": 0.0,
            "human_rate": 0.0,
            "avg_cost_units": 0.0,
            "avg_latency_ms": 0.0,
            "budget_exceeded_rate": 0.0,
            "by_category": {},
            "by_status": {},
        }

    n = len(runs)
    by_category: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for r in runs:
        by_category[r["category"]] = by_category.get(r["category"], 0) + 1
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1

    return {
        "runs": n,
        "resolved_rate": round(by_status.get("resolved", 0) / n, 4),
        "human_rate": round(sum(1 for r in runs if r.get("needs_human")) / n, 4),
        "avg_cost_units": round(
            sum(r.get("total_cost_units") or 0 for r in runs) / n, 6
        ),
        "avg_latency_ms": round(
            sum(r.get("total_latency_ms") or 0 for r in runs) / n, 2
        ),
        "budget_exceeded_rate": round(
            sum(1 for r in runs if r.get("budget_exceeded")) / n, 4
        ),
        "by_category": by_category,
        "by_status": by_status,
    }
