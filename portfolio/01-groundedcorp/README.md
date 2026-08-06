# GroundedCorp — grounded retrieval

Citation-first enterprise RAG. Hybrid BM25 + dense retrieval fused with reciprocal
rank fusion, reranked for phrase proximity, diversified with MMR, gated on grounding,
and guarded on both input and output.

**The design commitment: it refuses.** An assistant that confidently invents a leave
policy is worse than one that says it does not know.

## Pipeline

```
question
  → guard.input            block prompt injection before spending a token
  → analyse                strip stopwords, expand enterprise acronyms
  → BM25 (real IDF)  ┐
  → dense (hashing)  ┴→ RRF fusion
  → rerank                 phrase proximity + query-term coverage
  → relevance floor        drop candidates below 65% of the best
  → MMR                    trade relevance for coverage
  → pack                   cap the context window at a token budget
  → gate                   three independent floors → refuse, or continue
  → generate               grounded answer + citations
  → guard.output           PII redaction, secret blocking, grounding check
```

## Refusal reasons

Three independent floors, so a refusal is explainable rather than a shrug:

| `refusal_reason` | Meaning |
|---|---|
| `input_guard_blocked` | Prompt injection detected before retrieval ran |
| `no_evidence_retrieved` | Nothing came back from the index |
| `query_terms_absent_from_corpus` | The question's rare terms appear in no document |
| `no_passage_addresses_question` | Words match broadly, but no passage answers it |
| `grounding_below_threshold` | Composite grounding score under the floor |
| `output_guard_blocked` | The generated answer tripped an output guardrail |

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/ask` | Grounded answer with citations, cost and retrieval debug |
| `POST` | `/ask/stream` | SSE: retrieval → citations → tokens → guard verdict |
| `POST` | `/retrieve` | Retrieval without generation — the context-engineering debug view |
| `GET` | `/query/analyze` | Show the query rewrite and acronym expansions |
| `POST` | `/eval/run` | Golden-set evaluation with IR metrics and cost |
| `GET` | `/eval/drift` | Regression signal across eval history |
| `GET` | `/observability/metrics/prometheus` | Prometheus exposition |

## Run

```bash
python -m venv .venv && .venv/Scripts/activate   # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

python -m app.smoke      # ingest → ask → refuse → eval
python -m app.gate       # the CI quality gate
python -m pytest -q      # 16 tests

python -m uvicorn app.main:app --reload --port 8001
```

Use the [Frontier Console](../console/) for the UI, or `../docker-compose.yml` to
bring up the whole platform.

## Configuration

Every tunable is env-overridable, so the same image runs in dev, CI and a client
environment without a rebuild. See [`app/config.py`](./app/config.py) — `TOP_K`,
`BM25_K1`, `BM25_B`, `RRF_K`, `MMR_LAMBDA`, `RELATIVE_SCORE_FLOOR`,
`CONTEXT_TOKEN_BUDGET`, `GROUNDING_THRESHOLD`, `MIN_COVERAGE`, `MIN_RERANK_SCORE`.

Real model instead of the deterministic mock:

```bash
export LLM_PROVIDER=ollama
export LLM_BASE_URL=http://127.0.0.1:11434/v1
export LLM_MODEL=llama3.2
```
