"""Provider abstraction + a deterministic mock backend.

A ``Provider`` turns a list of chat messages into a completion. The default
registry routes by model id so one endpoint fronts many backends:

    mock-1, mock-flaky  -> MockProvider   (deterministic, no network, no key)
    gpt-*               -> OpenAIProvider  (optional, needs OPENAI_API_KEY)
    claude-*            -> AnthropicProvider (optional, needs ANTHROPIC_API_KEY)

Only the mock path runs in tests/CI, which is what keeps the suite fast,
offline and free.
"""
from __future__ import annotations

import re
from typing import Iterator, List, Protocol, Tuple

from .schemas import Message

_WORD_RE = re.compile(r"\S+")


def estimate_tokens(text: str) -> int:
    """Cheap, deterministic token estimate (~word count, min 1 for non-empty)."""
    if not text:
        return 0
    return max(1, len(_WORD_RE.findall(text)))


def messages_tokens(messages: List[Message]) -> int:
    return sum(estimate_tokens(m.content) for m in messages)


class ProviderError(RuntimeError):
    """Transient provider failure — eligible for retry."""


class Provider(Protocol):
    name: str

    def complete(
        self, model: str, messages: List[Message], temperature: float, max_tokens: int | None
    ) -> Tuple[str, str]:
        """Return ``(content, finish_reason)``."""

    def stream(
        self, model: str, messages: List[Message], temperature: float, max_tokens: int | None
    ) -> Iterator[str]:
        """Yield content chunks."""


class MockProvider:
    """Deterministic echo-style provider. Same input -> same output, always.

    ``mock-flaky`` fails ``flaky_failures`` times before succeeding, which is
    how the retry logic is exercised without any real network flakiness.
    """

    name = "mock"

    def __init__(self, flaky_failures: int = 2) -> None:
        self.flaky_failures = flaky_failures
        self._flaky_seen = 0

    def _answer(self, messages: List[Message]) -> str:
        last_user = next(
            (m.content for m in reversed(messages) if m.role == "user"), ""
        )
        n = estimate_tokens(last_user)
        return f"You said: {last_user!r}. (mock provider, {n} input tokens)"

    def complete(self, model, messages, temperature, max_tokens):
        if model == "mock-flaky" and self._flaky_seen < self.flaky_failures:
            self._flaky_seen += 1
            raise ProviderError(
                f"simulated transient error {self._flaky_seen}/{self.flaky_failures}"
            )
        text = self._answer(messages)
        if max_tokens is not None:
            words = _WORD_RE.findall(text)
            text = " ".join(words[:max_tokens])
        return text, "stop"

    def stream(self, model, messages, temperature, max_tokens):
        text, _ = self.complete(model, messages, temperature, max_tokens)
        for word in text.split(" "):
            yield word + " "


class OpenAIProvider:  # pragma: no cover - optional real path
    name = "openai"

    def __init__(self) -> None:
        from openai import OpenAI  # type: ignore

        self._client = OpenAI()

    def complete(self, model, messages, temperature, max_tokens):
        resp = self._client.chat.completions.create(
            model=model,
            messages=[m.model_dump() for m in messages],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        c = resp.choices[0]
        return (c.message.content or ""), (c.finish_reason or "stop")

    def stream(self, model, messages, temperature, max_tokens):
        stream = self._client.chat.completions.create(
            model=model,
            messages=[m.model_dump() for m in messages],
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
        for event in stream:
            delta = event.choices[0].delta.content
            if delta:
                yield delta


class AnthropicProvider:  # pragma: no cover - optional real path
    name = "anthropic"

    def __init__(self) -> None:
        import anthropic  # type: ignore

        self._client = anthropic.Anthropic()

    def _split(self, messages: List[Message]):
        system = " ".join(m.content for m in messages if m.role == "system")
        turns = [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role in ("user", "assistant")
        ]
        return system, turns

    def complete(self, model, messages, temperature, max_tokens):
        system, turns = self._split(messages)
        resp = self._client.messages.create(
            model=model,
            system=system or None,
            messages=turns,
            temperature=temperature,
            max_tokens=max_tokens or 1024,
        )
        return resp.content[0].text, (resp.stop_reason or "stop")

    def stream(self, model, messages, temperature, max_tokens):
        system, turns = self._split(messages)
        with self._client.messages.stream(
            model=model,
            system=system or None,
            messages=turns,
            temperature=temperature,
            max_tokens=max_tokens or 1024,
        ) as s:
            for text in s.text_stream:
                yield text


class Router:
    """Maps a model id to a provider by prefix, with a mock default.

    ``provider_name`` is pure (no SDK import), so listing models never requires
    a key. ``provider_for`` instantiates the real client lazily, only when a
    request actually routes to it.
    """

    def __init__(self) -> None:
        self._mock = MockProvider()
        self._openai: OpenAIProvider | None = None
        self._anthropic: AnthropicProvider | None = None

    @staticmethod
    def provider_name(model: str) -> str:
        if model.startswith("gpt-"):
            return "openai"
        if model.startswith("claude-"):
            return "anthropic"
        return "mock"  # mock* and any unknown model

    def provider_for(self, model: str) -> Provider:
        if model.startswith("gpt-"):  # pragma: no cover - optional
            if self._openai is None:
                self._openai = OpenAIProvider()
            return self._openai
        if model.startswith("claude-"):  # pragma: no cover - optional
            if self._anthropic is None:
                self._anthropic = AnthropicProvider()
            return self._anthropic
        # mock* and any unknown model fall back to the mock so the demo never
        # hard-fails on a missing key.
        return self._mock
