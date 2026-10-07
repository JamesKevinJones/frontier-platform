"""
Pluggable LLM providers: mock (default) | ollama | openai-compatible.

The abstraction exists so the whole platform can run, be tested and be demoed with
**no API key and no network** — `mock` is a deterministic extractive generator —
while the exact same call sites hit a real model when `LLM_PROVIDER` is set. That
property is what makes the CI quality gate meaningful: it runs on every push.

Every provider supports four call shapes:

    generate()              -> str                     (simple)
    generate_with_usage()   -> (str, Usage)            (cost-aware)
    generate_structured()   -> dict                    (structured outputs)
    stream()                -> Iterator[str]           (token streaming)

Reliability: network providers retry transient failures (connection errors, 429, 5xx;
not timeouts, which already spent the budget) with exponential backoff
and jitter, and surface a typed error rather than raising into the request path —
a degraded answer beats a 500 for a user-facing assistant.
"""

from __future__ import annotations

import json
import os
import random
import re
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from typing import Iterator

from .costs import Usage, estimate_tokens, estimate_usage

DEFAULT_SYSTEM_PROMPT = (
    "You are an enterprise knowledge assistant. Answer ONLY using the provided "
    "context excerpts. If the context is insufficient, say you cannot answer from "
    "the knowledge base — do not speculate. Cite source titles inline as [n]."
)

STRUCTURED_SYSTEM_PROMPT = (
    DEFAULT_SYSTEM_PROMPT
    + " Respond with a single JSON object matching the requested schema and nothing else."
)


class ProviderError(RuntimeError):
    """Raised when a provider exhausts its retries."""


class LLMProvider(ABC):
    name: str = "base"
    model: str = "mock"

    @abstractmethod
    def generate(self, question: str, contexts: list[dict]) -> str: ...

    def generate_with_usage(
        self, question: str, contexts: list[dict]
    ) -> tuple[str, Usage]:
        text = self.generate(question, contexts)
        return text, estimate_usage(
            build_prompt(question, contexts), text, model=self.model
        )

    def generate_structured(self, question: str, contexts: list[dict]) -> dict:
        """Structured output with a guaranteed shape, even if the model misbehaves."""
        text, _ = self.generate_with_usage(question, contexts)
        return coerce_structured(text, contexts)

    def stream(self, question: str, contexts: list[dict]) -> Iterator[str]:
        """Default: chunk a completed answer. Network providers override with real SSE."""
        text = self.generate(question, contexts)
        for token in re.findall(r"\S+\s*", text):
            yield token


def build_context_block(contexts: list[dict]) -> str:
    return "\n\n".join(
        f"[{i}] ({c.get('title', 'doc')}) {c['text']}"
        for i, c in enumerate(contexts, 1)
    )


def build_prompt(question: str, contexts: list[dict]) -> str:
    return f"{DEFAULT_SYSTEM_PROMPT}\n\nContext:\n{build_context_block(contexts)}\n\nQuestion: {question}"


def coerce_structured(text: str, contexts: list[dict]) -> dict:
    """Parse a model's JSON if it produced any; otherwise synthesise the envelope.

    Real models return JSON *most* of the time. Production code has to handle the
    rest, so the caller always receives the same shape.
    """
    candidate = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", candidate, re.DOTALL)
    if fenced:
        candidate = fenced.group(1)
    if candidate.startswith("{"):
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                parsed.setdefault("answer", text)
                parsed.setdefault("confidence", 0.5)
                parsed.setdefault("sources", [c.get("title", "") for c in contexts])
                parsed.setdefault("parsed", True)
                return parsed
        except json.JSONDecodeError:
            pass
    return {
        "answer": text,
        "confidence": 0.6 if contexts else 0.0,
        "sources": [c.get("title", "") for c in contexts],
        "parsed": False,
    }


class MockProvider(LLMProvider):
    """Deterministic extractive generation — no network, stable across runs.

    Ranks sentences from the retrieved contexts by query-token overlap and returns
    the best few, attributed. Because it can only emit text that came from the
    corpus, it cannot hallucinate, which makes it the right baseline for testing
    the *pipeline* (retrieval, grounding, guardrails) independently of the model.
    """

    name = "mock"
    model = "mock"

    def generate(self, question: str, contexts: list[dict]) -> str:
        if not contexts:
            return "I do not have enough grounded evidence in the knowledge base to answer."

        q_tokens = set(re.findall(r"[a-z0-9]+", question.lower()))
        scored: list[tuple[float, str, str]] = []
        for c in contexts:
            title = c.get("title", "doc")
            for sent in re.split(r"(?<=[.!?])\s+", c["text"]):
                s = sent.strip()
                if len(s) < 20:
                    continue
                st = set(re.findall(r"[a-z0-9]+", s.lower()))
                overlap = len(q_tokens & st) / max(len(q_tokens), 1)
                scored.append((overlap, s, title))
        scored.sort(key=lambda x: x[0], reverse=True)
        top = [s for s in scored if s[0] > 0][:3] or scored[:2]
        bullets = [f"- ({title}) {sent}" for _, sent, title in top]
        return (
            "Based on the indexed enterprise knowledge base:\n"
            + "\n".join(bullets)
            + "\n\nAll statements above are derived from retrieved source excerpts."
        )

    def generate_structured(self, question: str, contexts: list[dict]) -> dict:
        answer = self.generate(question, contexts)
        q_tokens = set(re.findall(r"[a-z0-9]+", question.lower()))
        evidence = set()
        for c in contexts:
            evidence.update(re.findall(r"[a-z0-9]+", c["text"].lower()))
        coverage = len(q_tokens & evidence) / max(len(q_tokens), 1)
        return {
            "answer": answer,
            "confidence": round(min(coverage, 1.0), 3),
            "sources": [c.get("title", "") for c in contexts],
            "parsed": True,
        }


class OpenAICompatibleProvider(LLMProvider):
    """Chat-completions client for Ollama or any OpenAI-compatible base URL."""

    name = "openai-compatible"

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout: float = 60.0,
        max_retries: int = 3,
        temperature: float = 0.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key or ""
        self.timeout = timeout
        self.max_retries = max_retries
        self.temperature = temperature

    # -- transport ----------------------------------------------------------

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _post(self, payload: dict) -> dict:
        """POST with exponential backoff + jitter on transient failures."""
        url = f"{self.base_url}/chat/completions"
        data = json.dumps(payload).encode("utf-8")
        last_error: Exception | None = None

        for attempt in range(self.max_retries):
            req = urllib.request.Request(
                url, data=data, headers=self._headers(), method="POST"
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                last_error = exc
                # 4xx other than rate-limit will not succeed on retry.
                if exc.code not in (408, 409, 429) and exc.code < 500:
                    break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                # A timeout already spent the whole budget on a slow server;
                # retrying it multiplies the wait and the server's load.
                if isinstance(exc, TimeoutError) or isinstance(
                    getattr(exc, "reason", None), TimeoutError
                ):
                    break

            if attempt < self.max_retries - 1:
                backoff = (2**attempt) * 0.5 + random.uniform(0, 0.25)
                time.sleep(backoff)

        raise ProviderError(f"{self.name} failed after {self.max_retries} attempts: {last_error}")

    def _messages(self, question: str, contexts: list[dict], system: str) -> list[dict]:
        return [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": f"Context:\n{build_context_block(contexts)}\n\nQuestion: {question}",
            },
        ]

    # -- call shapes --------------------------------------------------------

    def generate(self, question: str, contexts: list[dict]) -> str:
        text, _ = self.generate_with_usage(question, contexts)
        return text

    def generate_with_usage(
        self, question: str, contexts: list[dict]
    ) -> tuple[str, Usage]:
        if not contexts:
            return (
                "I do not have enough grounded evidence in the knowledge base to answer.",
                Usage(model=self.model),
            )
        payload = {
            "model": self.model,
            "messages": self._messages(question, contexts, DEFAULT_SYSTEM_PROMPT),
            "temperature": self.temperature,
        }
        try:
            body = self._post(payload)
        except ProviderError as exc:
            # Degrade, do not explode: the caller still gets a guarded response.
            return f"LLM provider unavailable ({self.name}): {exc}", Usage(model=self.model)

        try:
            text = body["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError) as exc:
            return f"LLM provider returned unexpected payload: {exc}", Usage(model=self.model)

        api_usage = body.get("usage") or {}
        usage = Usage(
            prompt_tokens=int(
                api_usage.get("prompt_tokens")
                or estimate_tokens(build_prompt(question, contexts))
            ),
            completion_tokens=int(
                api_usage.get("completion_tokens") or estimate_tokens(text)
            ),
            model=self.model,
        )
        return text, usage

    def generate_structured(self, question: str, contexts: list[dict]) -> dict:
        payload = {
            "model": self.model,
            "messages": self._messages(question, contexts, STRUCTURED_SYSTEM_PROMPT),
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
        }
        try:
            body = self._post(payload)
            text = body["choices"][0]["message"]["content"]
        except (ProviderError, KeyError, IndexError, TypeError):
            return super().generate_structured(question, contexts)
        return coerce_structured(text, contexts)

    def stream(self, question: str, contexts: list[dict]) -> Iterator[str]:
        """Real SSE streaming; falls back to chunked delivery if the server refuses."""
        payload = {
            "model": self.model,
            "messages": self._messages(question, contexts, DEFAULT_SYSTEM_PROMPT),
            "temperature": self.temperature,
            "stream": True,
        }
        url = f"{self.base_url}/chat/completions"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                for raw in resp:
                    line = raw.decode("utf-8").strip()
                    if not line.startswith("data:"):
                        continue
                    body = line[5:].strip()
                    if body == "[DONE]":
                        return
                    try:
                        delta = json.loads(body)["choices"][0]["delta"]
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
                    piece = delta.get("content")
                    if piece:
                        yield piece
        except (urllib.error.URLError, TimeoutError, OSError):
            yield from super().stream(question, contexts)


class OllamaProvider(OpenAICompatibleProvider):
    name = "ollama"

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434/v1",
        model: str = "llama3.2",
        api_key: str | None = "ollama",
        timeout: float = 60.0,
        max_retries: int = 3,
    ) -> None:
        super().__init__(
            base_url=base_url,
            model=model,
            api_key=api_key or "ollama",
            timeout=timeout,
            max_retries=max_retries,
        )


def get_provider(
    name: str | None = None,
    *,
    base_url: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
) -> LLMProvider:
    provider = (name or os.getenv("LLM_PROVIDER", "mock")).strip().lower()
    base = base_url if base_url is not None else os.getenv("LLM_BASE_URL", "")
    mdl = model if model is not None else os.getenv("LLM_MODEL", "")
    key = api_key if api_key is not None else os.getenv("LLM_API_KEY", "")

    if provider in ("mock", "", "deterministic"):
        return MockProvider()
    if provider == "ollama":
        return OllamaProvider(
            base_url=base or "http://127.0.0.1:11434/v1",
            model=mdl or "llama3.2",
            api_key=key or "ollama",
        )
    if provider in ("openai-compatible", "openai", "openai_compatible"):
        if not base:
            raise ValueError("LLM_BASE_URL is required for openai-compatible provider")
        return OpenAICompatibleProvider(
            base_url=base, model=mdl or "gpt-4o-mini", api_key=key or None
        )
    raise ValueError(f"Unknown LLM_PROVIDER={provider!r}; use mock|ollama|openai-compatible")
