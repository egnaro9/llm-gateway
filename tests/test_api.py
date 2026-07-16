import json


def _body(content="hello gateway", model="mock-1", **kw):
    return {"model": model, "messages": [{"role": "user", "content": content}], **kw}


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_auth_required(client):
    r = client.post("/v1/chat/completions", json=_body())
    assert r.status_code == 401


def test_bad_key_rejected(client):
    r = client.post(
        "/v1/chat/completions", json=_body(), headers={"Authorization": "Bearer nope"}
    )
    assert r.status_code == 401


def test_chat_completion_shape_and_determinism(client, auth):
    r = client.post("/v1/chat/completions", json=_body(), headers=auth)
    assert r.status_code == 200
    data = r.json()
    assert data["provider"] == "mock"
    assert data["choices"][0]["message"]["role"] == "assistant"
    assert "hello gateway" in data["choices"][0]["message"]["content"]
    assert data["usage"]["total_tokens"] == (
        data["usage"]["prompt_tokens"] + data["usage"]["completion_tokens"]
    )
    # Deterministic content across calls.
    r2 = client.post("/v1/chat/completions", json=_body(), headers=auth)
    assert r2.json()["choices"][0]["message"]["content"] == data["choices"][0]["message"]["content"]


def test_cache_hit_on_repeat(client, auth):
    first = client.post("/v1/chat/completions", json=_body(), headers=auth).json()
    second = client.post("/v1/chat/completions", json=_body(), headers=auth).json()
    assert first["cached"] is False
    assert second["cached"] is True
    m = client.get("/metrics").json()
    assert m["cache_hits"] >= 1
    assert m["cache_hit_rate"] > 0


def test_temperature_nonzero_not_cached(client, auth):
    a = client.post("/v1/chat/completions", json=_body(temperature=0.7), headers=auth).json()
    b = client.post("/v1/chat/completions", json=_body(temperature=0.7), headers=auth).json()
    assert a["cached"] is False and b["cached"] is False


def test_validation_rejects_empty_messages(client, auth):
    r = client.post("/v1/chat/completions", json={"model": "mock-1", "messages": []}, headers=auth)
    assert r.status_code == 422


def test_cost_accounting_priced_model(client, auth):
    # mock-pro is a priced model routed to the offline mock, so cost accounting
    # is exercised with no network. mock-1 is free.
    priced = client.post("/v1/chat/completions", json=_body(model="mock-pro"), headers=auth).json()
    free = client.post("/v1/chat/completions", json=_body(model="mock-1"), headers=auth).json()
    assert priced["provider"] == "mock"
    assert priced["cost_usd"] > 0
    assert free["cost_usd"] == 0.0


def test_models_endpoint(client):
    models = client.get("/v1/models").json()
    ids = {m["id"] for m in models}
    assert "mock-1" in ids and "gpt-4o-mini" in ids
    mock = next(m for m in models if m["id"] == "mock-1")
    assert mock["provider"] == "mock"


def test_streaming(client, auth):
    r = client.post("/v1/chat/completions", json=_body(stream=True), headers=auth)
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    lines = [ln for ln in r.text.splitlines() if ln.startswith("data: ")]
    assert lines[-1] == "data: [DONE]"
    deltas = [json.loads(ln[6:])["delta"] for ln in lines if ln != "data: [DONE]"]
    assert "hello" in "".join(deltas)


def test_request_id_echoed(client, auth):
    r = client.post(
        "/v1/chat/completions", json=_body(), headers={**auth, "x-request-id": "abc123"}
    )
    assert r.headers["x-request-id"] == "abc123"


def test_flaky_model_recovers_via_retry(client, auth):
    # mock-flaky fails twice then succeeds; default retries=3 should ride it out.
    r = client.post("/v1/chat/completions", json=_body(model="mock-flaky"), headers=auth)
    assert r.status_code == 200
    assert "hello gateway" in r.json()["choices"][0]["message"]["content"]
