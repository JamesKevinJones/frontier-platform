"""Provider layer — re-exported from ``shared.llm`` so all three apps share one
implementation of retries, cost accounting, streaming and structured outputs.

Kept as a module here (rather than deleted) because it is the seam a client
environment would edit: swap in an Azure AI Foundry or Vertex AI provider by
subclassing ``LLMProvider`` and extending ``get_provider`` below, without touching
the RAG pipeline.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PORTFOLIO = Path(__file__).resolve().parents[2]
if str(_PORTFOLIO) not in sys.path:
    sys.path.insert(0, str(_PORTFOLIO))

from shared.llm import (  # noqa: E402
    DEFAULT_SYSTEM_PROMPT,
    LLMProvider,
    MockProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
    ProviderError,
    Usage,
    build_context_block,
    build_prompt,
    coerce_structured,
    get_provider,
)

__all__ = [
    "DEFAULT_SYSTEM_PROMPT",
    "LLMProvider",
    "MockProvider",
    "OllamaProvider",
    "OpenAICompatibleProvider",
    "ProviderError",
    "Usage",
    "build_context_block",
    "build_prompt",
    "coerce_structured",
    "get_provider",
]
