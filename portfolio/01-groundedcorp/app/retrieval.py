"""
Context engineering: the retrieval half of RAG, which is where answer quality is
actually won or lost.

Pipeline
--------
    query -> analyse/expand -> BM25 (lexical) ─┐
                            -> dense (vector) ─┴─> RRF fusion -> rerank -> MMR -> pack

Why each stage exists
---------------------
**Query analysis.** Enterprise questions carry acronyms ("PTO", "MFA", "SEV-1")
that a hashing embedder has never seen in context. Expanding them before retrieval
is far cheaper than a better embedding model.

**BM25 with real IDF.** The previous scorer was raw term-frequency over length,
which rates a term appearing in every document as highly as a rare one. IDF is the
whole point of lexical retrieval: it is what makes "accrual" outrank "the".

**Reciprocal Rank Fusion.** Vector and lexical scores live on incomparable scales,
so a weighted sum of them silently tracks whichever has the larger variance. RRF
fuses on *rank* instead, needs no tuning per corpus, and is what most production
hybrid stacks use.

**Rerank.** A cheap cross-encoder stand-in: rewards phrase proximity and query-term
coverage within the passage rather than bag-of-words overlap. This is the stage that
promotes the passage that actually answers the question over one that merely
mentions its words.

**MMR.** Top-k by score alone returns four near-duplicate chunks of the same
paragraph. Maximal Marginal Relevance trades a little relevance for coverage, so
the citation list is worth reading.

**Packing.** Contexts are truncated to a token budget — an unbounded context window
is a cost and latency regression, and beyond a point a recall regression too.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

from app.config import (
    BM25_B,
    BM25_K1,
    CONTEXT_TOKEN_BUDGET,
    MMR_LAMBDA,
    RELATIVE_SCORE_FLOOR,
    RRF_K,
    TOP_K,
)
from app.embeddings import cosine, embed_text
from app.vectorstore import get_store

# ---------------------------------------------------------------------------
# Query analysis
# ---------------------------------------------------------------------------

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does", "for",
    "from", "how", "i", "in", "is", "it", "long", "many", "much", "must", "of",
    "on", "or", "our", "should", "the", "to", "we", "what", "when", "where",
    "which", "who", "why", "will", "with", "you", "your",
}

# Enterprise vocabulary a general-purpose embedder will not have grounded.
ACRONYMS = {
    "pto": ["paid time off", "leave", "vacation", "accrual"],
    "mfa": ["multi factor authentication", "two factor", "2fa", "authentication"],
    "sso": ["single sign on", "identity provider"],
    "sev": ["severity", "incident"],
    "sev1": ["severity 1", "critical incident", "escalate"],
    "sev-1": ["severity 1", "critical incident", "escalate"],
    "pii": ["personally identifiable information", "customer data", "privacy"],
    "llm": ["large language model", "ai tool", "generative ai"],
    "rbac": ["role based access control", "permission", "role"],
    "vpn": ["remote access", "network"],
    "ic": ["incident commander"],
    "sla": ["service level agreement", "response time"],
    "iam": ["identity access management", "access"],
}

_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def content_terms(text: str) -> list[str]:
    """Query tokens that actually carry retrieval signal."""
    return [t for t in tokenize(text) if t not in STOPWORDS and len(t) > 1]


@dataclass
class AnalyzedQuery:
    original: str
    terms: list[str]
    expansions: list[str] = field(default_factory=list)

    @property
    def expanded_text(self) -> str:
        return " ".join([self.original, *self.expansions])

    def to_dict(self) -> dict:
        return {
            "original": self.original,
            "terms": self.terms,
            "expansions": self.expansions,
            "rewritten": self.expanded_text,
        }


def analyze_query(question: str) -> AnalyzedQuery:
    """Strip noise, expand known enterprise acronyms. Deterministic, no model call."""
    terms = content_terms(question)
    expansions: list[str] = []
    seen: set[str] = set()
    for term in terms:
        for expansion in ACRONYMS.get(term, []):
            if expansion not in seen:
                seen.add(expansion)
                expansions.append(expansion)
    return AnalyzedQuery(original=question, terms=terms, expansions=expansions)


# ---------------------------------------------------------------------------
# BM25
# ---------------------------------------------------------------------------


class BM25Index:
    """Okapi BM25 over the chunk corpus. Rebuilt on ingest, cached per store size."""

    def __init__(self, documents: list[str]) -> None:
        self.docs_tokens = [tokenize(d) for d in documents]
        self.n_docs = len(self.docs_tokens) or 1
        self.doc_len = [len(t) for t in self.docs_tokens]
        self.avg_doc_len = (sum(self.doc_len) / self.n_docs) or 1.0
        self.term_freqs = [Counter(t) for t in self.docs_tokens]
        df: Counter[str] = Counter()
        for tokens in self.docs_tokens:
            df.update(set(tokens))
        self.doc_freq = df

    def idf(self, term: str) -> float:
        df = self.doc_freq.get(term, 0)
        # BM25+ style smoothing keeps IDF non-negative for very common terms.
        return math.log(1 + (self.n_docs - df + 0.5) / (df + 0.5))

    def score(self, terms: list[str], doc_index: int) -> float:
        tf = self.term_freqs[doc_index]
        length = self.doc_len[doc_index] or 1
        total = 0.0
        for term in terms:
            freq = tf.get(term, 0)
            if not freq:
                continue
            numerator = freq * (BM25_K1 + 1)
            denominator = freq + BM25_K1 * (
                1 - BM25_B + BM25_B * length / self.avg_doc_len
            )
            total += self.idf(term) * numerator / denominator
        return total

    def coverage(self, terms: list[str], doc_index: int) -> float:
        """IDF-weighted fraction of query terms present — the grounding signal."""
        if not terms:
            return 0.0
        tf = self.term_freqs[doc_index]
        weights = {t: self.idf(t) for t in terms}
        total_weight = sum(weights.values()) or 1.0
        hit_weight = sum(w for t, w in weights.items() if tf.get(t))
        return hit_weight / total_weight


_bm25_cache: tuple[int, BM25Index] | None = None


def get_bm25(documents: list[str]) -> BM25Index:
    global _bm25_cache
    if _bm25_cache is None or _bm25_cache[0] != len(documents):
        _bm25_cache = (len(documents), BM25Index(documents))
    return _bm25_cache[1]


def reset_bm25() -> None:
    global _bm25_cache
    _bm25_cache = None


# ---------------------------------------------------------------------------
# Fusion, rerank, diversify
# ---------------------------------------------------------------------------


def reciprocal_rank_fusion(*ranked_lists: list[int], k: int = RRF_K) -> dict[int, float]:
    """RRF: score = sum over lists of 1/(k + rank). Scale-free, no tuning per corpus."""
    fused: dict[int, float] = {}
    for ranking in ranked_lists:
        for rank, doc_index in enumerate(ranking, start=1):
            fused[doc_index] = fused.get(doc_index, 0.0) + 1.0 / (k + rank)
    return fused


def rerank_score(terms: list[str], text: str) -> float:
    """Cheap cross-encoder stand-in: coverage + phrase proximity + early-position bonus.

    A passage that contains every query term inside one sentence answers the
    question; one that scatters them across 400 words probably does not.
    """
    if not terms:
        return 0.0
    tokens = tokenize(text)
    if not tokens:
        return 0.0

    positions: dict[str, list[int]] = {}
    for i, tok in enumerate(tokens):
        if tok in terms:
            positions.setdefault(tok, []).append(i)

    coverage = len(positions) / len(set(terms))
    if not positions:
        return 0.0

    # Tightest window containing the most distinct query terms.
    flat = sorted((pos, tok) for tok, plist in positions.items() for pos in plist)
    best_window = len(tokens)
    distinct = len(positions)
    left = 0
    window_counts: Counter[str] = Counter()
    for right, (pos, tok) in enumerate(flat):
        window_counts[tok] += 1
        while len(window_counts) == distinct:
            span = pos - flat[left][0] + 1
            best_window = min(best_window, span)
            window_counts[flat[left][1]] -= 1
            if window_counts[flat[left][1]] == 0:
                del window_counts[flat[left][1]]
            left += 1

    proximity = 1.0 / (1.0 + math.log1p(max(best_window - distinct, 0)))
    earliest = flat[0][0] / len(tokens)
    position_bonus = 1.0 - min(earliest, 1.0)

    return round(0.55 * coverage + 0.35 * proximity + 0.10 * position_bonus, 6)


def mmr_select(
    candidates: list[dict],
    top_k: int = TOP_K,
    lambda_param: float = MMR_LAMBDA,
) -> list[dict]:
    """Maximal Marginal Relevance — relevance traded against redundancy."""
    if not candidates:
        return []

    pool = list(candidates)
    selected: list[dict] = []
    embeddings = {id(c): embed_text(c["text"]) for c in pool}

    while pool and len(selected) < top_k:
        best, best_value = None, float("-inf")
        for candidate in pool:
            relevance = candidate["score"]
            redundancy = max(
                (
                    cosine(embeddings[id(candidate)], embeddings[id(s)])
                    for s in selected
                ),
                default=0.0,
            )
            # Same source document is redundant even if the wording differs.
            if any(s["doc_id"] == candidate["doc_id"] for s in selected):
                redundancy = max(redundancy, 0.55)
            value = lambda_param * relevance - (1 - lambda_param) * redundancy
            if value > best_value:
                best, best_value = candidate, value
        selected.append(best)
        pool.remove(best)
    return selected


def pack_contexts(
    contexts: list[dict], token_budget: int = CONTEXT_TOKEN_BUDGET
) -> list[dict]:
    """Trim the context window to a budget. Highest-scoring passages get the room."""
    packed: list[dict] = []
    used = 0
    for c in contexts:
        cost = len(tokenize(c["text"]))
        if used + cost > token_budget:
            remaining_words = max(token_budget - used, 0)
            if remaining_words < 40:
                break
            trimmed = " ".join(c["text"].split()[:remaining_words])
            packed.append({**c, "text": trimmed, "truncated": True})
            break
        packed.append({**c, "truncated": False})
        used += cost
    return packed


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


@dataclass
class RetrievalResult:
    contexts: list[dict]
    query: AnalyzedQuery
    coverage: float
    top_rerank: float
    candidates_considered: int
    stages: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "query": self.query.to_dict(),
            "coverage": round(self.coverage, 4),
            "top_rerank": round(self.top_rerank, 4),
            "candidates_considered": self.candidates_considered,
            "stages": self.stages,
        }


def retrieve(
    question: str, dept: str | None = None, top_k: int = TOP_K
) -> RetrievalResult:
    analyzed = analyze_query(question)
    store = get_store()

    if store.count() == 0:
        return RetrievalResult([], analyzed, 0.0, 0.0, 0, {"reason": "empty_index"})

    documents = store.documents
    metadatas = store.metadatas
    bm25 = get_bm25(documents)

    eligible = [
        i
        for i, meta in enumerate(metadatas)
        if not dept or meta.get("dept") == dept
    ]
    if not eligible:
        return RetrievalResult([], analyzed, 0.0, 0.0, 0, {"reason": "dept_filter_empty"})

    search_terms = content_terms(analyzed.expanded_text)
    pool_size = min(max(top_k * 6, 24), len(eligible))

    # --- lexical ranking ---
    lexical_scores = {i: bm25.score(search_terms, i) for i in eligible}
    lexical_rank = sorted(
        (i for i in eligible if lexical_scores[i] > 0),
        key=lambda i: lexical_scores[i],
        reverse=True,
    )[:pool_size]

    # --- dense ranking ---
    q_emb = embed_text(analyzed.expanded_text)
    dense_scores = {i: cosine(q_emb, store.embeddings[i]) for i in eligible}
    dense_rank = sorted(eligible, key=lambda i: dense_scores[i], reverse=True)[:pool_size]

    # --- fusion ---
    fused = reciprocal_rank_fusion(lexical_rank, dense_rank)
    fused_top = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:pool_size]

    # --- rerank ---
    # RRF deliberately discards score magnitude, which is what makes it robust to
    # incomparable scales. But it also throws away a real signal: when one passage
    # outscores the field on BM25 by an order of magnitude, that *is* the answer.
    # So the final score re-admits max-normalised BM25 alongside the fused rank.
    original_terms = content_terms(question) or search_terms
    max_bm25 = max((lexical_scores.get(i, 0.0) for i, _ in fused_top), default=0.0) or 1.0
    max_fusion = max((s for _, s in fused_top), default=0.0) or 1.0

    candidates: list[dict] = []
    for doc_index, fusion_score in fused_top:
        text = documents[doc_index]
        meta = metadatas[doc_index]
        rr = rerank_score(original_terms, text)
        expanded_rr = rerank_score(search_terms, text)
        blended = max(rr, 0.8 * expanded_rr)
        bm25_raw = lexical_scores.get(doc_index, 0.0)
        candidates.append(
            {
                "text": text,
                "doc_id": meta["doc_id"],
                "title": meta["title"],
                "dept": meta["dept"],
                "chunk_index": meta.get("chunk_index", 0),
                "score": round(
                    0.45 * blended
                    + 0.32 * (bm25_raw / max_bm25)
                    + 0.23 * (fusion_score / max_fusion),
                    6,
                ),
                "rerank_score": round(blended, 6),
                "bm25": round(bm25_raw, 4),
                "bm25_norm": round(bm25_raw / max_bm25, 4),
                "dense": round(dense_scores.get(doc_index, 0.0), 4),
                "fusion": round(fusion_score, 6),
            }
        )
    candidates.sort(key=lambda c: c["score"], reverse=True)

    # --- relevance floor ---
    # MMR will happily fill the last slots with weakly-related passages just to
    # add diversity. Padding the citation list with near-misses trains users to
    # stop reading citations, so drop anything far below the best candidate.
    if candidates:
        cutoff = candidates[0]["score"] * RELATIVE_SCORE_FLOOR
        # Always keep the best candidate; the gate below decides whether it is
        # good enough to answer from at all.
        candidates = [c for c in candidates if c["score"] >= cutoff] or candidates[:1]

    # --- diversify + pack ---
    selected = mmr_select(candidates[: max(top_k * 3, 12)], top_k=top_k)
    packed = pack_contexts(selected)

    best_index = fused_top[0][0] if fused_top else None
    coverage = bm25.coverage(original_terms, best_index) if best_index is not None else 0.0
    top_rerank = max((c["rerank_score"] for c in packed), default=0.0)

    return RetrievalResult(
        contexts=packed,
        query=analyzed,
        coverage=round(coverage, 4),
        top_rerank=round(top_rerank, 4),
        candidates_considered=len(candidates),
        stages={
            "lexical_hits": len(lexical_rank),
            "dense_hits": len(dense_rank),
            "fused": len(fused_top),
            "after_mmr": len(selected),
            "after_packing": len(packed),
            "expansions": analyzed.expansions,
        },
    )


def grounding_score(result: RetrievalResult) -> float:
    """Single 0-1 confidence that the corpus can answer this question.

    Deliberately weighted toward IDF coverage: a question whose rare terms are
    absent from every document is unanswerable no matter how well the remaining
    common words match. That is the signal that drives refusal.
    """
    if not result.contexts:
        return 0.0
    return round(0.6 * result.coverage + 0.4 * min(result.top_rerank, 1.0), 4)
