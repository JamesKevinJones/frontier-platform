"""GroundedCorp API — enterprise RAG with citations, guardrails and refusal."""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app.config import SERVICE_NAME, TOP_K
from app.drift import detect_drift
from app.eval_harness import GATE, get_eval_run, list_eval_history, run_eval
from app.ingest import ingest_corpus, list_documents
from app.llm import get_active_provider
from app.models import AskRequest, AskResponse, DriftReport, EvalSummary
from app.rag import ask, stream_answer
from app.retrieval import analyze_query
from app.retrieval import retrieve as retrieve_full

_PORTFOLIO = Path(__file__).resolve().parents[2]
if str(_PORTFOLIO) not in sys.path:
    sys.path.insert(0, str(_PORTFOLIO))

from shared.guardrails import active_policy  # noqa: E402
from shared.llm import get_cache  # noqa: E402
from shared.observability import get_tracer, install_observability  # noqa: E402

tracer = get_tracer(SERVICE_NAME)


@asynccontextmanager
async def lifespan(_: FastAPI):
    ingest_corpus()
    yield


app = FastAPI(
    title="GroundedCorp",
    version="2.0.0",
    description=(
        "Citation-first enterprise RAG. Hybrid retrieval (BM25 + dense) fused with "
        "RRF, reranked, diversified with MMR, gated on grounding, guarded on both "
        "input and output."
    ),
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Trace-Id", "traceparent"],
)
install_observability(app, tracer)


@app.get("/health", tags=["ops"])
def health():
    provider = get_active_provider()
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "version": app.version,
        "provider": provider.name,
        "model": getattr(provider, "model", "mock"),
        "cache": get_cache().stats(),
        "guard_policy": {
            "grounding_threshold": active_policy().grounding_threshold,
            "blocking_severities": list(active_policy().blocking_severities),
        },
    }


@app.post("/ingest", tags=["knowledge"])
def ingest():
    with tracer.span("ingest.corpus"):
        return ingest_corpus()


@app.get("/docs/list", tags=["knowledge"])
def docs():
    return list_documents()


@app.post("/ask", response_model=AskResponse, tags=["rag"])
def ask_endpoint(body: AskRequest):
    return ask(
        body.question,
        dept=body.dept,
        structured=body.structured,
        use_cache=body.use_cache,
    )


@app.post("/ask/stream", tags=["rag"])
def ask_stream(body: AskRequest):
    """Server-sent events: retrieval -> citations -> tokens -> guard verdict."""
    return StreamingResponse(
        stream_answer(body.question, dept=body.dept),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/retrieve", tags=["rag"])
def retrieve_endpoint(body: AskRequest):
    """Retrieval without generation — the debugging view of context engineering."""
    result = retrieve_full(body.question, dept=body.dept, top_k=TOP_K)
    return {**result.to_dict(), "contexts": result.contexts}


@app.get("/query/analyze", tags=["rag"])
def analyze(q: str):
    return analyze_query(q).to_dict()


@app.post("/eval/run", response_model=EvalSummary, tags=["evaluation"])
def eval_run():
    with tracer.span("eval.run"):
        summary = run_eval()
    tracer.set_gauge("eval.citation_hit_rate", summary.citation_hit_rate)
    tracer.set_gauge("eval.faithfulness", summary.avg_faithfulness)
    tracer.set_gauge("eval.mrr", summary.mrr)
    return summary


@app.get("/eval/history", tags=["evaluation"])
def eval_history(limit: int = 20):
    return list_eval_history(limit=limit)


@app.get("/eval/runs/{run_id}", tags=["evaluation"])
def eval_run_detail(run_id: int):
    data = get_eval_run(run_id)
    if data is None:
        raise HTTPException(status_code=404, detail="eval run not found")
    return data


@app.get("/eval/gate", tags=["evaluation"])
def eval_gate():
    return {"thresholds": GATE}


@app.get("/eval/drift", response_model=DriftReport, tags=["evaluation"])
def eval_drift(window: int = 5):
    return detect_drift(window=window)


@app.post("/cache/clear", tags=["ops"])
def cache_clear():
    get_cache().clear()
    return {"cleared": True, "cache": get_cache().stats()}
