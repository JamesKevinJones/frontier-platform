"""Two-tier response cache: exact-match, then semantic near-match.

Why two tiers
-------------
Exact-match hashing catches the repeated-question case (dashboards polling, users
re-running a demo) at zero risk. Semantic matching catches paraphrases — "PTO
accrual rate?" vs "how much PTO do I accrue?" — which is where most of the real
saving is, but it can serve a subtly wrong answer if the threshold is loose. So
the semantic tier is gated on a deliberately high cosine threshold (0.93 default)
and every hit is labelled in the response, never silently.

Entries are TTL'd because grounded answers go stale when the corpus is re-ingested.
"""

from __future__ import annotations

import hashlib
import os
import time
from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock
from typing import Callable

DEFAULT_TTL_SECONDS = float(os.getenv("LLM_CACHE_TTL", "900"))
DEFAULT_MAX_ENTRIES = int(os.getenv("LLM_CACHE_MAX", "256"))
DEFAULT_SIMILARITY = float(os.getenv("LLM_CACHE_SIMILARITY", "0.93"))


@dataclass
class CacheEntry:
    key: str
    value: str
    embedding: list[float] | None
    created_at: float

    def expired(self, ttl: float) -> bool:
        return (time.time() - self.created_at) > ttl


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5 or 1.0
    nb = sum(x * x for x in b) ** 0.5 or 1.0
    return dot / (na * nb)


class ResponseCache:
    def __init__(
        self,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        similarity_threshold: float = DEFAULT_SIMILARITY,
    ) -> None:
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self.similarity_threshold = similarity_threshold
        self._store: "OrderedDict[str, CacheEntry]" = OrderedDict()
        self._lock = Lock()
        self.hits_exact = 0
        self.hits_semantic = 0
        self.misses = 0

    @staticmethod
    def make_key(*parts: str) -> str:
        return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()

    def get(
        self,
        key: str,
        embedding: list[float] | None = None,
    ) -> tuple[str | None, str]:
        """Return ``(value, hit_kind)`` where hit_kind is exact | semantic | miss."""
        with self._lock:
            self._evict_expired()

            entry = self._store.get(key)
            if entry is not None:
                self._store.move_to_end(key)
                self.hits_exact += 1
                return entry.value, "exact"

            if embedding:
                best, best_sim = None, 0.0
                for candidate in self._store.values():
                    if not candidate.embedding:
                        continue
                    sim = _cosine(embedding, candidate.embedding)
                    if sim > best_sim:
                        best, best_sim = candidate, sim
                if best is not None and best_sim >= self.similarity_threshold:
                    self.hits_semantic += 1
                    return best.value, "semantic"

            self.misses += 1
            return None, "miss"

    def put(self, key: str, value: str, embedding: list[float] | None = None) -> None:
        with self._lock:
            self._store[key] = CacheEntry(
                key=key, value=value, embedding=embedding, created_at=time.time()
            )
            self._store.move_to_end(key)
            while len(self._store) > self.max_entries:
                self._store.popitem(last=False)

    def get_or_compute(
        self,
        key: str,
        compute: Callable[[], str],
        embedding: list[float] | None = None,
    ) -> tuple[str, str]:
        value, kind = self.get(key, embedding)
        if value is not None:
            return value, kind
        value = compute()
        self.put(key, value, embedding)
        return value, "miss"

    def _evict_expired(self) -> None:
        stale = [k for k, e in self._store.items() if e.expired(self.ttl_seconds)]
        for k in stale:
            del self._store[k]

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def stats(self) -> dict:
        total = self.hits_exact + self.hits_semantic + self.misses
        hits = self.hits_exact + self.hits_semantic
        return {
            "entries": len(self._store),
            "max_entries": self.max_entries,
            "ttl_seconds": self.ttl_seconds,
            "similarity_threshold": self.similarity_threshold,
            "hits_exact": self.hits_exact,
            "hits_semantic": self.hits_semantic,
            "misses": self.misses,
            "hit_rate": round(hits / total, 4) if total else 0.0,
        }


_default_cache = ResponseCache()


def get_cache() -> ResponseCache:
    return _default_cache
