"""LLM entrypoint — resolves the active provider once and routes generation calls."""

from __future__ import annotations

from app.providers import LLMProvider, Usage, get_provider

_provider: LLMProvider | None = None


def get_active_provider() -> LLMProvider:
    global _provider
    if _provider is None:
        _provider = get_provider()
    return _provider


def reset_provider() -> None:
    """Clear the cached provider — used by tests and by env changes at runtime."""
    global _provider
    _provider = None


def generate_grounded_answer(question: str, contexts: list[dict]) -> str:
    return get_active_provider().generate(question, contexts)


def generate_with_usage(question: str, contexts: list[dict]) -> tuple[str, Usage]:
    return get_active_provider().generate_with_usage(question, contexts)


def generate_structured_answer(question: str, contexts: list[dict]) -> dict:
    """Typed envelope: ``{answer, confidence, sources, parsed}``."""
    return get_active_provider().generate_structured(question, contexts)


def stream_tokens(question: str, contexts: list[dict]):
    return get_active_provider().stream(question, contexts)
