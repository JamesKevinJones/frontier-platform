"""Deterministic local embeddings — no model download, no numpy."""

from __future__ import annotations

import hashlib
import math
import re

from app.config import EMBED_DIM

_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def embed_text(text: str, dim: int = EMBED_DIM) -> list[float]:
    """Hashing-trick embedding — stable, fast, offline."""
    vec = [0.0] * dim
    toks = tokenize(text)
    if not toks:
        return vec
    for tok in toks:
        h = hashlib.sha256(tok.encode()).digest()
        idx = int.from_bytes(h[:4], "little") % dim
        sign = 1.0 if h[4] % 2 == 0 else -1.0
        vec[idx] += sign
    for a, b in zip(toks, toks[1:]):
        h = hashlib.sha256(f"{a}_{b}".encode()).digest()
        idx = int.from_bytes(h[:4], "little") % dim
        vec[idx] += 0.5
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)
