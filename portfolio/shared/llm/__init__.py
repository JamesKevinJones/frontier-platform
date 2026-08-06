"""Pluggable LLM providers with cost accounting, caching and structured outputs."""

from .cache import ResponseCache, get_cache
from .costs import PRICE_TABLE, Usage, estimate_tokens, estimate_usage
from .providers import (
    DEFAULT_SYSTEM_PROMPT,
    LLMProvider,
    MockProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
    ProviderError,
    build_context_block,
    build_prompt,
    coerce_structured,
    get_provider,
)

__all__ = [
    "DEFAULT_SYSTEM_PROMPT",
    "PRICE_TABLE",
    "LLMProvider",
    "MockProvider",
    "OllamaProvider",
    "OpenAICompatibleProvider",
    "ProviderError",
    "ResponseCache",
    "Usage",
    "build_context_block",
    "build_prompt",
    "coerce_structured",
    "estimate_tokens",
    "estimate_usage",
    "get_cache",
    "get_provider",
]
