"""The optional SQLAlchemy usage store, the Alembic migration, and the in-memory
default that keeps the offline (SQLAlchemy-free) browser demo working."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from llmgateway.app import Config, Metrics, create_app
from llmgateway.schemas import Usage
from llmgateway.store import SqlUsageStore

REPO = Path(__file__).resolve().parent.parent


def _body(content="hello", model="mock-1"):
    return {"model": model, "messages": [{"role": "user", "content": content}]}


def _usage(p=10, c=5):
    return Usage(prompt_tokens=p, completion_tokens=c, total_tokens=p + c)


# --- the default path the browser demo relies on ---------------------------

def test_no_database_url_uses_in_memory_metrics():
    app = create_app(Config(api_keys=frozenset({"dev-key"})))
    assert isinstance(app.state.metrics, Metrics)   # in-memory, no ORM imported


# --- the SQL store aggregates correctly ------------------------------------

def test_sql_store_records_and_aggregates(tmp_path):
    store = SqlUsageStore(f"sqlite:///{tmp_path / 'g.db'}")
    store.record("gpt-4o", _usage(10, 5), 0.002, cached=False, latency_ms=12.0)
    store.record("gpt-4o", _usage(0, 0), 0.0, cached=True)
    store.record("mock-1", _usage(4, 2), 0.0001, cached=False)
    store.record_rate_limited()

    snap = store.snapshot()
    assert snap.requests == 3                     # the rate-limit marker isn't a request
    assert snap.rate_limited == 1
    assert snap.cache_hits == 1
    assert snap.cache_hit_rate == round(1 / 3, 4)
    assert snap.total_tokens == 10 + 5 + 0 + 4 + 2
    assert snap.total_cost_usd == round(0.002 + 0.0 + 0.0001, 6)
    assert snap.by_model["gpt-4o"].requests == 2
    assert "-" not in snap.by_model               # the rate-limit sentinel is excluded


# --- persistence survives a restart (the whole point) ----------------------

def test_metrics_persist_across_app_instances(tmp_path):
    url = f"sqlite:///{tmp_path / 'g.db'}"
    cfg = Config(api_keys=frozenset({"dev-key"}), rate_capacity=1000, database_url=url)
    auth = {"Authorization": "Bearer dev-key"}

    c1 = TestClient(create_app(cfg))
    assert c1.post("/v1/chat/completions", json=_body(), headers=auth).status_code == 200
    assert c1.get("/metrics").json()["requests"] == 1

    # A brand-new app on the same database still sees the request — it lives in
    # the table, not in this process's memory.
    c2 = TestClient(create_app(cfg))
    assert c2.get("/metrics").json()["requests"] == 1


# --- the migration produces a schema the app actually runs on --------------

def test_alembic_migration_creates_working_schema(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config as AlembicConfig

    url = f"sqlite:///{tmp_path / 'm.db'}"
    monkeypatch.setenv("GATEWAY_DATABASE_URL", url)   # env.py reads this
    cfg = AlembicConfig(str(REPO / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO / "migrations"))
    command.upgrade(cfg, "head")

    # create=False: prove the app runs on the *migrated* schema, not a create_all one.
    store = SqlUsageStore(url, create=False)
    store.record("gpt-4o", _usage(3, 1), 0.001, cached=False)
    assert store.snapshot().requests == 1
