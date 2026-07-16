import pytest

from llmgateway.cache import ResponseCache, cache_key
from llmgateway.pricing import cost_usd
from llmgateway.providers import MockProvider, ProviderError, estimate_tokens
from llmgateway.retry import RetryStats, with_retry
from llmgateway.schemas import (
    ChatResponse,
    Choice,
    Message,
    Usage,
)


def _resp(model="mock-1"):
    usage = Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2)
    return ChatResponse(
        id="x",
        model=model,
        provider="mock",
        choices=[Choice(index=0, message=Message(role="assistant", content="hi"), finish_reason="stop")],
        usage=usage,
        cost_usd=0.0,
        latency_ms=1.0,
    )


def test_cache_key_is_stable_and_content_sensitive():
    m1 = [Message(role="user", content="a")]
    m2 = [Message(role="user", content="b")]
    assert cache_key("mock-1", m1, None) == cache_key("mock-1", m1, None)
    assert cache_key("mock-1", m1, None) != cache_key("mock-1", m2, None)
    assert cache_key("mock-1", m1, None) != cache_key("gpt-4o", m1, None)


def test_lru_eviction():
    cache = ResponseCache(maxsize=2)
    cache.set("a", _resp())
    cache.set("b", _resp())
    cache.get("a")           # touch 'a' so 'b' is now least-recently-used
    cache.set("c", _resp())  # evicts 'b'
    assert cache.get("a") is not None
    assert cache.get("b") is None
    assert cache.get("c") is not None


def test_cost_is_zero_for_mock_and_positive_for_priced():
    usage = Usage(prompt_tokens=1000, completion_tokens=1000, total_tokens=2000)
    assert cost_usd("mock-1", usage) == 0.0
    # gpt-4o-mini: 0.00015 prompt + 0.00060 completion per 1k -> 0.00075 for 1k+1k
    assert cost_usd("gpt-4o-mini", usage) == pytest.approx(0.00075)


def test_estimate_tokens():
    assert estimate_tokens("") == 0
    assert estimate_tokens("one two three") == 3


def test_retry_succeeds_after_transient_failures():
    stats = RetryStats()
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ProviderError("boom")
        return "ok"

    out = with_retry(flaky, retries=3, base_delay=0.0, exceptions=(ProviderError,), sleep=lambda _: None, stats=stats)
    assert out == "ok"
    assert calls["n"] == 3
    assert stats.attempts == 3


def test_retry_gives_up_and_reraises():
    def always_fails():
        raise ProviderError("nope")

    with pytest.raises(ProviderError):
        with_retry(always_fails, retries=2, base_delay=0.0, exceptions=(ProviderError,), sleep=lambda _: None)


def test_mock_flaky_provider_then_recovers():
    p = MockProvider(flaky_failures=2)
    msgs = [Message(role="user", content="hi")]
    with pytest.raises(ProviderError):
        p.complete("mock-flaky", msgs, 0.0, None)
    with pytest.raises(ProviderError):
        p.complete("mock-flaky", msgs, 0.0, None)
    content, finish = p.complete("mock-flaky", msgs, 0.0, None)
    assert finish == "stop"
    assert "hi" in content
