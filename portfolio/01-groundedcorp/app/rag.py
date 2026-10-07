"""
Grounded question answering: retrieve -> gate -> generate -> guard.

The ordering is the point. Guardrails run on the way *in* (before we spend a token
on a prompt-injection attempt) and on the way *out* (before a user sees anything).
The grounding gate sits between them: if the corpus cannot support an answer, the
service refuses instead of letting the model improvise. An enterprise assistant
that confidently invents a leave policy is worse than one that says "I don't know".
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

from app.config import (
    CACHE_ENABLED,
    GROUNDING_THRESHOLD,
    GUARD_GROUNDING_THRESHOLD,
    MIN_COVERAGE,
    MIN_RERANK_SCORE,
    SERVICE_NAME,
    TOP_K,
)
from app.embeddings import embed_text
from app.models import (
    AskResponse,
    Citation,
    GuardFindingOut,
    RetrievalDebug,
    StructuredAnswer,
    UsageOut,
)
from app.retrieval import RetrievalResult
from app.retrieval import grounding_score as _grounding_score
from app.retrieval import retrieve as _retrieve_full

_PORTFOLIO = Path(__file__).resolve().parents[2]
if str(_PORTFOLIO) not in sys.path:
    sys.path.insert(0, str(_PORTFOLIO))

from shared.guardrails import validate  # noqa: E402
from shared.llm import Usage, get_cache  # noqa: E402
from shared.observability import current_trace_id, get_tracer  # noqa: E402

from app.llm import (  # noqa: E402  (must follow sys.path bootstrap)
    generate_grounded_answer,
    generate_structured_answer,
    get_active_provider,
)

tracer = get_tracer(SERVICE_NAME)

REFUSAL_TEXT = (
    "Insufficient grounded evidence in the knowledge base to answer this question. "
    "Try rephrasing, selecting a different department, or ingesting the relevant policy document."
)


# ---------------------------------------------------------------------------
# Backwards-compatible helpers
# ---------------------------------------------------------------------------


def retrieve(question: str, dept: str | None = None, top_k: int = TOP_K) -> list[dict]:
    """Context passages only — the shape the eval harness and tests expect."""
    return _retrieve_full(question, dept=dept, top_k=top_k).contexts


def grounding_score(question: str, contexts: list[dict]) -> float:
    """Kept for callers that already hold contexts rather than a RetrievalResult."""
    if not contexts:
        return 0.0
    result = _retrieve_full(question)
    return _grounding_score(result)


def faithfulness_proxy(answer: str, contexts: list[dict]) -> float:
    """Offline faithfulness: what share of the answer's *content* words are supported
    by retrieved evidence.

    Stopwords are excluded — including them inflates every score toward 1.0 and
    makes the metric useless for comparing runs. This is a proxy for an NLI-based
    faithfulness judge, chosen because it runs in CI with no model and no key.
    """
    from app.retrieval import STOPWORDS

    if not contexts or not answer.strip():
        return 0.0
    a_toks = {
        t
        for t in re.findall(r"[a-z0-9]+", answer.lower())
        if t not in STOPWORDS and len(t) > 2
    }
    if not a_toks:
        return 0.0
    e_toks: set[str] = set()
    for c in contexts:
        e_toks.update(re.findall(r"[a-z0-9]+", c["text"].lower()))
    return round(len(a_toks & e_toks) / len(a_toks), 4)


def _findings_out(result) -> list[GuardFindingOut]:
    return [
        GuardFindingOut(rule=f.rule, severity=f.severity, message=f.message, span=f.span)
        for f in result.findings
    ]


def _debug(result: RetrievalResult) -> RetrievalDebug:
    return RetrievalDebug(
        rewritten_query=result.query.expanded_text,
        expansions=result.query.expansions,
        coverage=result.coverage,
        top_rerank=result.top_rerank,
        candidates_considered=result.candidates_considered,
        stages=result.stages,
    )


def _citations(contexts: list[dict]) -> list[Citation]:
    return [
        Citation(
            doc_id=c["doc_id"],
            title=c["title"],
            dept=c["dept"],
            excerpt=c["text"][:280] + ("…" if len(c["text"]) > 280 else ""),
            score=round(c["score"], 4),
            rerank_score=round(c.get("rerank_score", 0.0), 4),
            bm25=round(c.get("bm25", 0.0), 4),
            dense=round(c.get("dense", 0.0), 4),
            truncated=bool(c.get("truncated")),
        )
        for c in contexts
    ]


def _should_refuse(result: RetrievalResult, score: float) -> str | None:
    """Three independent floors. Any one of them failing means we do not answer.

    Using several weak signals rather than one composite makes the refusal
    explainable — the response says *which* floor was missed.
    """
    if not result.contexts:
        return "no_evidence_retrieved"
    if result.coverage < MIN_COVERAGE:
        return "query_terms_absent_from_corpus"
    if result.top_rerank < MIN_RERANK_SCORE:
        return "no_passage_addresses_question"
    if score < GROUNDING_THRESHOLD:
        return "grounding_below_threshold"
    return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def ask(
    question: str,
    dept: str | None = None,
    *,
    structured: bool = False,
    use_cache: bool = True,
) -> AskResponse:
    started = time.perf_counter()
    provider = get_active_provider()
    cache = get_cache()

    def elapsed() -> float:
        return round((time.perf_counter() - started) * 1000, 2)

    with tracer.span("rag.ask", dept=dept or "all", structured=structured) as root:
        root.set_attribute("question.length", len(question))

        # --- 1. input guard: block before spending retrieval or tokens --------
        with tracer.span("guard.input"):
            in_guard = validate(question, direction="input")
        if not in_guard.allowed:
            tracer.incr("ask.blocked_input")
            root.set_attribute("outcome", "blocked_input")
            return AskResponse(
                answer="Request blocked by input guardrails.",
                grounded=False,
                grounding_score=0.0,
                citations=[],
                refused=True,
                refusal_reason="input_guard_blocked",
                guarded=True,
                guard_findings=_findings_out(in_guard),
                provider=provider.name,
                trace_id=current_trace_id(),
                latency_ms=elapsed(),
            )

        # --- 2. retrieval + context engineering ------------------------------
        with tracer.span("rag.retrieve", top_k=TOP_K) as span:
            result = _retrieve_full(question, dept=dept)
            span.set_attribute("contexts", len(result.contexts))
            span.set_attribute("coverage", result.coverage)
            span.set_attribute("expansions", result.query.expansions)
        score = _grounding_score(result)
        tracer.observe("rag.grounding_score", score)

        # --- 3. grounding gate ------------------------------------------------
        refusal = _should_refuse(result, score)
        if refusal:
            tracer.incr("ask.refused")
            root.set_attribute("outcome", f"refused:{refusal}")
            out_guard = validate(
                REFUSAL_TEXT,
                direction="output",
                grounding_score=score,
                grounding_threshold=GUARD_GROUNDING_THRESHOLD,
            )
            return AskResponse(
                answer=REFUSAL_TEXT,
                grounded=False,
                grounding_score=score,
                citations=[],
                refused=True,
                refusal_reason=refusal,
                guarded=True,
                guard_findings=_findings_out(out_guard),
                provider=provider.name,
                trace_id=current_trace_id(),
                latency_ms=elapsed(),
                retrieval=_debug(result),
            )

        # --- 4. generation (cache-aware) -------------------------------------
        contexts = result.contexts
        cache_state = "disabled"
        structured_payload: StructuredAnswer | None = None
        usage = Usage(model=getattr(provider, "model", provider.name))

        # Scope is everything an answer depends on except the wording of the
        # question; the semantic tier only reuses answers within one scope.
        cache_scope = cache.make_key(
            provider.name,
            getattr(provider, "model", ""),
            dept or "",
            str(structured),
            "|".join(f"{c['doc_id']}#{c.get('chunk_index', 0)}" for c in contexts),
        )
        cache_key = cache.make_key(cache_scope, question.strip().lower())

        cached_answer = None
        if CACHE_ENABLED and use_cache:
            with tracer.span("cache.lookup"):
                cached_answer, cache_state = cache.get(cache_key, embed_text(question), cache_scope)
                tracer.incr(f"cache.{cache_state}")

        if cached_answer is not None:
            answer = cached_answer
            usage.cached = True
            if structured:
                structured_payload = StructuredAnswer(
                    answer=answer,
                    confidence=score,
                    sources=[c["title"] for c in contexts],
                    parsed=True,
                )
        else:
            with tracer.span("llm.generate", provider=provider.name) as span:
                if structured:
                    payload = generate_structured_answer(question, contexts)
                    structured_payload = StructuredAnswer(**payload)
                    answer = structured_payload.answer
                    usage = Usage(
                        model=getattr(provider, "model", provider.name),
                        prompt_tokens=sum(len(c["text"].split()) for c in contexts),
                        completion_tokens=len(answer.split()),
                    )
                else:
                    answer, usage = provider.generate_with_usage(question, contexts)
                span.set_attribute("answer.length", len(answer))
                span.set_attribute("cost_usd", usage.cost_usd)
            if CACHE_ENABLED and use_cache:
                cache.put(cache_key, answer, embed_text(question), cache_scope)
            cache_state = cache_state if cache_state != "disabled" else "miss"

        tracer.observe("llm.cost_usd", usage.cost_usd)
        tracer.observe("llm.total_tokens", usage.total_tokens)

        # --- 5. output guard --------------------------------------------------
        with tracer.span("guard.output"):
            out_guard = validate(
                answer,
                direction="output",
                grounding_score=score,
                grounding_threshold=GUARD_GROUNDING_THRESHOLD,
            )
        safe_answer = out_guard.redacted_text or answer
        blocked = not out_guard.allowed
        if blocked:
            tracer.incr("ask.blocked_output")
        root.set_attribute("outcome", "blocked_output" if blocked else "answered")

        return AskResponse(
            answer="Output blocked by guardrails." if blocked else safe_answer,
            grounded=not blocked,
            grounding_score=score,
            citations=[] if blocked else _citations(contexts),
            refused=blocked,
            refusal_reason="output_guard_blocked" if blocked else None,
            guarded=True,
            guard_findings=_findings_out(out_guard),
            provider=provider.name,
            trace_id=current_trace_id(),
            latency_ms=elapsed(),
            cache=cache_state,
            usage=UsageOut(**usage.to_dict()),
            retrieval=_debug(result),
            structured=None if blocked else structured_payload,
        )


def stream_answer(question: str, dept: str | None = None):
    """Server-sent-event generator: emit retrieval and guard stages as they happen,
    then stream the answer. Perceived latency matters as much as real latency."""
    import json

    provider = get_active_provider()

    in_guard = validate(question, direction="input")
    if not in_guard.allowed:
        yield f"event: blocked\ndata: {json.dumps({'reason': 'input_guard_blocked'})}\n\n"
        return

    result = _retrieve_full(question, dept=dept)
    score = _grounding_score(result)
    yield "event: retrieval\ndata: " + json.dumps(
        {
            "contexts": len(result.contexts),
            "coverage": result.coverage,
            "grounding_score": score,
            "expansions": result.query.expansions,
        }
    ) + "\n\n"

    refusal = _should_refuse(result, score)
    if refusal:
        yield f"event: refused\ndata: {json.dumps({'reason': refusal, 'grounding_score': score})}\n\n"
        return

    yield "event: citations\ndata: " + json.dumps(
        [c.model_dump() for c in _citations(result.contexts)]
    ) + "\n\n"

    buffer: list[str] = []
    for token in provider.stream(question, result.contexts):
        buffer.append(token)
        yield f"event: token\ndata: {json.dumps({'t': token})}\n\n"

    full = "".join(buffer)
    out_guard = validate(
        full,
        direction="output",
        grounding_score=score,
        grounding_threshold=GUARD_GROUNDING_THRESHOLD,
    )
    yield "event: done\ndata: " + json.dumps(
        {
            "allowed": out_guard.allowed,
            "grounding_score": score,
            "risk_score": out_guard.risk_score,
            "provider": provider.name,
            "findings": [f.to_dict() for f in out_guard.findings],
        }
    ) + "\n\n"
