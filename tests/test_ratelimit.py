from fastapi.testclient import TestClient

from llmgateway.app import Config, create_app
from llmgateway.ratelimit import RateLimiter


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_bucket_blocks_then_refills():
    clock = FakeClock()
    rl = RateLimiter(capacity=2, refill_per_sec=1.0, now=clock)
    assert rl.allow("k") is True
    assert rl.allow("k") is True
    assert rl.allow("k") is False  # bucket empty
    assert rl.retry_after("k") == 1.0
    clock.t = 1.0  # one token refilled
    assert rl.allow("k") is True


def test_buckets_are_per_key():
    clock = FakeClock()
    rl = RateLimiter(capacity=1, refill_per_sec=1.0, now=clock)
    assert rl.allow("a") is True
    assert rl.allow("a") is False
    assert rl.allow("b") is True  # independent bucket


def test_gateway_returns_429_when_exhausted():
    # capacity 2, no refill within the test window.
    cfg = Config(api_keys=frozenset({"dev-key"}), rate_capacity=2, rate_refill_per_sec=0.0)
    client = TestClient(create_app(cfg))
    h = {"Authorization": "Bearer dev-key"}
    body = {"model": "mock-1", "messages": [{"role": "user", "content": "hi"}]}
    assert client.post("/v1/chat/completions", json=body, headers=h).status_code == 200
    assert client.post("/v1/chat/completions", json=body, headers=h).status_code == 200
    r = client.post("/v1/chat/completions", json=body, headers=h)
    assert r.status_code == 429
    assert "Retry-After" in r.headers
    assert client.get("/metrics").json()["rate_limited"] == 1
