"""Optional SQLAlchemy-backed usage store.

By default the gateway counts requests in memory — a dict behind a lock, which
is all the offline Pyodide demo can use (it installs `fastapi` and nothing
else). Point ``GATEWAY_DATABASE_URL`` at a database and the gateway instead
writes **one row per request** and answers ``/metrics`` with a ``GROUP BY`` over
that table, so the numbers survive a restart, add up across more than one
instance, and can be sliced after the fact (per model, per hour, cache-hit rate
over time).

SQLAlchemy is imported *here and only here*. Importing this module is itself the
opt-in — the default in-memory path in ``app.py`` never touches an ORM, so the
browser demo keeps working. The store exposes the same three methods as the
in-memory ``Metrics`` (``record`` / ``record_rate_limited`` / ``snapshot``), so
the request handler is identical whichever backend is configured.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Integer, String, create_engine, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .schemas import MetricsResponse, ModelUsage, Usage

# A rate-limited request never reaches a provider, so it has no model. It's still
# recorded (to count 429s) under this sentinel, and excluded from request/token
# aggregates the same way the in-memory store keeps `rate_limited` separate.
_RATE_LIMIT_MODEL = "-"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class UsageRow(Base):
    """One billed (or rate-limited) request. The grain is deliberately fine —
    aggregate up in SQL rather than storing pre-summed counters, so a question
    nobody asked yet ("p95 latency for gpt-4o last Tuesday") is still answerable."""

    __tablename__ = "usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model: Mapped[str] = mapped_column(String(128), index=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    rate_limited: Mapped[bool] = mapped_column(Boolean, default=False)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=_utcnow, index=True)


class SqlUsageStore:
    """Drop-in replacement for the in-memory ``Metrics``, backed by a table."""

    def __init__(self, database_url: str, *, create: bool = True) -> None:
        # future=True is 2.0-style; pool_pre_ping survives a connection dropped
        # by a serverless Postgres (Neon) that closed an idle socket.
        self._engine = create_engine(database_url, future=True, pool_pre_ping=True)
        self._Session = sessionmaker(self._engine, future=True, expire_on_commit=False)
        # For a fresh SQLite file this is convenient; a real Postgres deploy runs
        # the Alembic migration instead and passes create=False.
        if create:
            Base.metadata.create_all(self._engine)

    def record(self, model: str, usage: Usage, cost: float,
               cached: bool, latency_ms: float = 0.0) -> None:
        with self._Session.begin() as s:
            s.add(UsageRow(
                model=model,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                cost_usd=cost,
                cache_hit=cached,
                latency_ms=latency_ms,
            ))

    def record_rate_limited(self) -> None:
        with self._Session.begin() as s:
            s.add(UsageRow(model=_RATE_LIMIT_MODEL, rate_limited=True))

    def snapshot(self) -> MetricsResponse:
        served = UsageRow.rate_limited.is_(False)  # a real, non-throttled request
        with self._Session() as s:
            requests = s.scalar(select(func.count()).where(served)) or 0
            cache_hits = s.scalar(
                select(func.count()).where(UsageRow.cache_hit.is_(True))) or 0
            rate_limited = s.scalar(
                select(func.count()).where(UsageRow.rate_limited.is_(True))) or 0
            total_tokens = s.scalar(select(func.coalesce(
                func.sum(UsageRow.prompt_tokens + UsageRow.completion_tokens), 0)
            ).where(served)) or 0

            by_model = {
                model: ModelUsage(
                    requests=n,
                    prompt_tokens=int(pt),
                    completion_tokens=int(ct),
                    cost_usd=round(float(cost), 6),
                )
                for model, n, pt, ct, cost in s.execute(
                    select(
                        UsageRow.model,
                        func.count(),
                        func.coalesce(func.sum(UsageRow.prompt_tokens), 0),
                        func.coalesce(func.sum(UsageRow.completion_tokens), 0),
                        func.coalesce(func.sum(UsageRow.cost_usd), 0.0),
                    ).where(served).group_by(UsageRow.model)
                ).all()
            }

            total_cost = round(sum(m.cost_usd for m in by_model.values()), 6)
            hit_rate = round(cache_hits / requests, 4) if requests else 0.0
            return MetricsResponse(
                requests=requests,
                cache_hits=cache_hits,
                cache_hit_rate=hit_rate,
                rate_limited=rate_limited,
                total_tokens=int(total_tokens),
                total_cost_usd=total_cost,
                by_model=by_model,
            )
