"""
Offline evaluation harness.

Measures the pipeline on three axes, because optimising one alone is how RAG
systems quietly get worse:

    retrieval quality   precision@k, recall@k, MRR   — did we find the right passage?
    answer quality      faithfulness, citation hit   — did we use it honestly?
    operations          p95 latency, cost per query  — can we afford to run it?

Every run is persisted to SQLite so ``/eval/drift`` can compare today against a
baseline. A single eval score is a number; a *series* is a regression signal.
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone

from app.config import GOLDEN_PATH, SERVICE_NAME, SQLITE_PATH
from app.models import EvalSummary
from app.rag import ask, faithfulness_proxy, retrieve

# Quality gate. CI fails the build when a run drops below these.
GATE = {
    "min_citation_hit_rate": 0.85,
    "min_faithfulness": 0.30,
    "min_mrr": 0.75,
    "min_precision_at_k": 0.60,
    "max_p95_latency_ms": 2500.0,
}


def _connect() -> sqlite3.Connection:
    SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(SQLITE_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS eval_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            n INTEGER NOT NULL,
            avg_grounding REAL NOT NULL,
            refuse_rate REAL NOT NULL,
            citation_hit_rate REAL NOT NULL,
            avg_faithfulness REAL NOT NULL,
            avg_latency_ms REAL NOT NULL,
            details_json TEXT NOT NULL
        )
        """
    )
    # Additive migration: keeps existing databases readable after the upgrade.
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(eval_runs)")}
    for column, ddl in (
        ("p95_latency_ms", "REAL NOT NULL DEFAULT 0"),
        ("avg_cost_usd", "REAL NOT NULL DEFAULT 0"),
        ("total_cost_usd", "REAL NOT NULL DEFAULT 0"),
        ("precision_at_k", "REAL NOT NULL DEFAULT 0"),
        ("recall_at_k", "REAL NOT NULL DEFAULT 0"),
        ("mrr", "REAL NOT NULL DEFAULT 0"),
        ("gate_passed", "INTEGER NOT NULL DEFAULT 1"),
        ("provider", "TEXT NOT NULL DEFAULT 'mock'"),
    ):
        if column not in existing:
            conn.execute(f"ALTER TABLE eval_runs ADD COLUMN {column} {ddl}")
    conn.commit()
    return conn


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(max(int(len(ordered) * q) - 1, 0), len(ordered) - 1)
    return round(ordered[idx], 2)


def _retrieval_metrics(cited: list[str], expected: set[str]) -> tuple[float, float, float]:
    """precision@k, recall@k, reciprocal rank — the standard IR triple."""
    if not expected:
        return 0.0, 0.0, 0.0
    retrieved = list(dict.fromkeys(cited))
    if not retrieved:
        return 0.0, 0.0, 0.0
    relevant = [d for d in retrieved if d in expected]
    precision = len(relevant) / len(retrieved)
    recall = len(set(relevant)) / len(expected)
    rr = 0.0
    for rank, doc_id in enumerate(retrieved, start=1):
        if doc_id in expected:
            rr = 1.0 / rank
            break
    return precision, recall, rr


def run_eval() -> EvalSummary:
    data = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    details: list[dict] = []
    latencies: list[float] = []
    grounding_sum = refuses = citation_hits = 0.0
    faithfulness_sum = cost_sum = 0.0
    precision_sum = recall_sum = mrr_sum = 0.0
    scored_retrieval = 0
    provider_name = "mock"

    for item in data:
        t0 = time.perf_counter()
        # Cache is bypassed: an eval that measures cache hits measures nothing.
        resp = ask(item["question"], dept=item.get("dept"), use_cache=False)
        latency_ms = (time.perf_counter() - t0) * 1000
        provider_name = resp.provider or provider_name

        contexts = (
            retrieve(item["question"], dept=item.get("dept")) if not resp.refused else []
        )
        faith = faithfulness_proxy(resp.answer, contexts) if contexts else 0.0

        expected = set(item.get("expect_doc_ids") or [])
        cited = [c.doc_id for c in resp.citations]

        if item.get("expect_refuse"):
            hit = resp.refused
        elif expected:
            hit = bool(expected & set(cited))
        else:
            hit = not resp.refused

        if expected and not item.get("expect_refuse"):
            precision, recall, rr = _retrieval_metrics(cited, expected)
            precision_sum += precision
            recall_sum += recall
            mrr_sum += rr
            scored_retrieval += 1
        else:
            precision = recall = rr = 0.0

        grounding_sum += resp.grounding_score
        refuses += int(resp.refused)
        citation_hits += int(hit)
        faithfulness_sum += faith
        cost_sum += resp.usage.cost_usd
        latencies.append(latency_ms)

        details.append(
            {
                "id": item["id"],
                "question": item["question"],
                "refused": resp.refused,
                "refusal_reason": resp.refusal_reason,
                "grounding": resp.grounding_score,
                "faithfulness": faith,
                "latency_ms": round(latency_ms, 2),
                "cost_usd": resp.usage.cost_usd,
                "tokens": resp.usage.total_tokens,
                "hit": hit,
                "precision_at_k": round(precision, 4),
                "recall_at_k": round(recall, 4),
                "reciprocal_rank": round(rr, 4),
                "cited": cited,
                "expected": sorted(expected),
                "coverage": resp.retrieval.coverage,
                "expansions": resp.retrieval.expansions,
                "guard_findings": [f.model_dump() for f in resp.guard_findings],
            }
        )

    n = len(data) or 1
    r = max(scored_retrieval, 1)
    summary = EvalSummary(
        n=len(data),
        avg_grounding=round(grounding_sum / n, 4),
        refuse_rate=round(refuses / n, 4),
        citation_hit_rate=round(citation_hits / n, 4),
        avg_faithfulness=round(faithfulness_sum / n, 4),
        avg_latency_ms=round(sum(latencies) / n, 2),
        p95_latency_ms=_percentile(latencies, 0.95),
        avg_cost_usd=round(cost_sum / n, 8),
        total_cost_usd=round(cost_sum, 8),
        precision_at_k=round(precision_sum / r, 4),
        recall_at_k=round(recall_sum / r, 4),
        mrr=round(mrr_sum / r, 4),
        details=details,
    )

    failures = evaluate_gate(summary)
    summary.gate_failures = failures
    summary.gate_passed = not failures
    summary.run_id = persist_eval_run(summary, provider=provider_name)
    return summary


def evaluate_gate(summary: EvalSummary) -> list[str]:
    """Absolute quality floors. Returns a list of human-readable failures."""
    failures: list[str] = []
    if summary.citation_hit_rate < GATE["min_citation_hit_rate"]:
        failures.append(
            f"citation_hit_rate {summary.citation_hit_rate} < {GATE['min_citation_hit_rate']}"
        )
    if summary.avg_faithfulness < GATE["min_faithfulness"]:
        failures.append(
            f"avg_faithfulness {summary.avg_faithfulness} < {GATE['min_faithfulness']}"
        )
    if summary.mrr < GATE["min_mrr"]:
        failures.append(f"mrr {summary.mrr} < {GATE['min_mrr']}")
    if summary.precision_at_k < GATE["min_precision_at_k"]:
        failures.append(
            f"precision_at_k {summary.precision_at_k} < {GATE['min_precision_at_k']}"
        )
    if summary.p95_latency_ms > GATE["max_p95_latency_ms"]:
        failures.append(
            f"p95_latency_ms {summary.p95_latency_ms} > {GATE['max_p95_latency_ms']}"
        )
    return failures


def persist_eval_run(summary: EvalSummary, provider: str = "mock") -> int:
    conn = _connect()
    cur = conn.execute(
        """
        INSERT INTO eval_runs (
            created_at, n, avg_grounding, refuse_rate, citation_hit_rate,
            avg_faithfulness, avg_latency_ms, details_json,
            p95_latency_ms, avg_cost_usd, total_cost_usd,
            precision_at_k, recall_at_k, mrr, gate_passed, provider
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            datetime.now(timezone.utc).isoformat(),
            summary.n,
            summary.avg_grounding,
            summary.refuse_rate,
            summary.citation_hit_rate,
            summary.avg_faithfulness,
            summary.avg_latency_ms,
            json.dumps(summary.details),
            summary.p95_latency_ms,
            summary.avg_cost_usd,
            summary.total_cost_usd,
            summary.precision_at_k,
            summary.recall_at_k,
            summary.mrr,
            int(summary.gate_passed),
            provider,
        ),
    )
    conn.commit()
    run_id = int(cur.lastrowid)
    conn.close()
    return run_id


def list_eval_history(limit: int = 20) -> list[dict]:
    conn = _connect()
    rows = conn.execute(
        """
        SELECT id, created_at, n, avg_grounding, refuse_rate, citation_hit_rate,
               avg_faithfulness, avg_latency_ms, p95_latency_ms, avg_cost_usd,
               total_cost_usd, precision_at_k, recall_at_k, mrr, gate_passed, provider
        FROM eval_runs
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_eval_run(run_id: int) -> dict | None:
    conn = _connect()
    row = conn.execute("SELECT * FROM eval_runs WHERE id = ?", (run_id,)).fetchone()
    conn.close()
    if row is None:
        return None
    data = dict(row)
    data["details"] = json.loads(data.pop("details_json", "[]"))
    return data


SERVICE = SERVICE_NAME
