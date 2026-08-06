"""Token and cost accounting.

Cost is a first-class production signal for LLM systems: a change that improves
answer quality by 2% but triples spend is usually a regression. Every generation
on this platform carries a ``Usage`` record so the eval harness can report
quality *and* cost per question, and the CI gate can fail on a cost regression.

Prices are USD per 1M tokens, kept in one table so a model swap is a data change.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

# USD per 1M tokens: (prompt, completion)
PRICE_TABLE: dict[str, tuple[float, float]] = {
    "mock": (0.0, 0.0),
    "llama3.2": (0.0, 0.0),          # local Ollama — compute cost, not token cost
    "qwen2.5": (0.0, 0.0),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5": (3.00, 15.00),
}

DEFAULT_PRICE = (0.50, 1.50)

_WORD = re.compile(r"\w+|[^\w\s]")


def estimate_tokens(text: str) -> int:
    """Tokenizer-free estimate. Within ~10% of BPE for English prose.

    Deliberately not `len(text) // 4`: that under-counts code and punctuation-heavy
    payloads, which is exactly where RAG contexts live.
    """
    if not text:
        return 0
    pieces = _WORD.findall(text)
    # Long words split into multiple subword tokens.
    return sum(max(1, math.ceil(len(p) / 4)) if len(p) > 6 else 1 for p in pieces)


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = "mock"
    cached: bool = False

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def cost_usd(self) -> float:
        if self.cached:
            return 0.0
        prompt_rate, completion_rate = PRICE_TABLE.get(self.model, DEFAULT_PRICE)
        cost = (
            self.prompt_tokens * prompt_rate + self.completion_tokens * completion_rate
        ) / 1_000_000
        return round(cost, 8)

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": self.cost_usd,
            "cached": self.cached,
        }

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            model=self.model if self.model == other.model else "mixed",
            cached=self.cached and other.cached,
        )


def estimate_usage(prompt: str, completion: str, model: str = "mock") -> Usage:
    return Usage(
        prompt_tokens=estimate_tokens(prompt),
        completion_tokens=estimate_tokens(completion),
        model=model,
    )
