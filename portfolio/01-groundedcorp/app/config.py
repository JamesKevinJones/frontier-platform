"""Configuration. Every tunable is env-overridable so the same image runs in
dev, CI and a client environment without a rebuild."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
CORPUS_DIR = DATA_DIR / "corpus"
CHROMA_DIR = DATA_DIR / "chroma"
SQLITE_PATH = DATA_DIR / "groundedcorp.db"
GOLDEN_PATH = DATA_DIR / "golden_eval.json"

SERVICE_NAME = "groundedcorp"
HOST_PORT = int(os.getenv("PORT", "8001"))


def _f(name: str, default: float) -> float:
    return float(os.getenv(name, default))


def _i(name: str, default: int) -> int:
    return int(os.getenv(name, default))


# --- chunking ---------------------------------------------------------------
CHUNK_SIZE = _i("CHUNK_SIZE", 220)
CHUNK_OVERLAP = _i("CHUNK_OVERLAP", 50)

# --- embeddings -------------------------------------------------------------
EMBED_DIM = _i("EMBED_DIM", 256)

# --- retrieval --------------------------------------------------------------
TOP_K = _i("TOP_K", 4)
BM25_K1 = _f("BM25_K1", 1.5)           # term-frequency saturation
BM25_B = _f("BM25_B", 0.75)            # length normalisation
RRF_K = _i("RRF_K", 60)                # standard RRF constant
MMR_LAMBDA = _f("MMR_LAMBDA", 0.72)    # relevance vs diversity
CONTEXT_TOKEN_BUDGET = _i("CONTEXT_TOKEN_BUDGET", 900)
# Drop candidates scoring below this fraction of the best candidate, so the
# citation list never gets padded with near-misses just to reach top_k.
RELATIVE_SCORE_FLOOR = _f("RELATIVE_SCORE_FLOOR", 0.65)

# --- grounding / refusal ----------------------------------------------------
# Below this, the service refuses rather than answering from weak evidence.
GROUNDING_THRESHOLD = _f("GROUNDING_THRESHOLD", 0.30)
# Independent floor on the reranker: catches the case where common words match
# broadly but no passage actually addresses the question.
MIN_RERANK_SCORE = _f("MIN_RERANK_SCORE", 0.30)
MIN_COVERAGE = _f("MIN_COVERAGE", 0.28)
# Shared guardrails apply their own (stricter) grounding gate on the way out.
GUARD_GROUNDING_THRESHOLD = _f("GUARD_GROUNDING_THRESHOLD", 0.35)

# --- cost / caching ---------------------------------------------------------
CACHE_ENABLED = os.getenv("CACHE_ENABLED", "true").strip().lower() in {"1", "true", "yes"}
