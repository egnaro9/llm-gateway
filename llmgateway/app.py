"""The FastAPI gateway.

One OpenAI-shaped endpoint (``POST /v1/chat/completions``) fronts many
providers and layers on the things a raw provider SDK doesn't give you:

    auth  ->  rate limit  ->  cache  ->  retry  ->  cost accounting  ->  metrics

Everything is deterministic against the built-in mock provider, so the whole
thing runs with no API key and CI stays green and free.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from threading import Lock
from typing import Dict, Iterator, List

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from . import __version__
from .cache import ResponseCache, cache_key, tenant_id
from .pricing import PRICING, cost_usd, price_for
from .providers import Router, messages_tokens
from .ratelimit import RateLimiter
from .retry import with_retry
from .schemas import (
    ChatRequest,
    ChatResponse,
    Choice,
    HealthResponse,
    Message,
    MetricsResponse,
    ModelInfo,
    ModelUsage,
    Usage,
)

logger = logging.getLogger("llmgateway")


# --------------------------------------------------------------------------- #
# Config + in-memory metrics
# --------------------------------------------------------------------------- #
@dataclass
class Config:
    api_keys: frozenset[str] = field(default_factory=lambda: frozenset({"dev-key"}))
    rate_capacity: int = 60
    rate_refill_per_sec: float = 1.0
    cache_size: int = 512
    retries: int = 3
    database_url: str | None = None

    @classmethod
    def from_env(cls) -> "Config":
        keys = os.environ.get("GATEWAY_API_KEYS", "dev-key")
        return cls(
            api_keys=frozenset(k.strip() for k in keys.split(",") if k.strip()),
            rate_capacity=int(os.environ.get("GATEWAY_RATE_CAPACITY", "60")),
            rate_refill_per_sec=float(os.environ.get("GATEWAY_RATE_REFILL", "1.0")),
            cache_size=int(os.environ.get("GATEWAY_CACHE_SIZE", "512")),
            # Unset -> in-memory metrics (and the browser demo needs no ORM). Set
            # it (e.g. postgresql+psycopg://… or sqlite:///gateway.db) to persist.
            database_url=os.environ.get("GATEWAY_DATABASE_URL") or None,
        )


@dataclass
class _ModelStat:
    requests: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0


class Metrics:
    def __init__(self) -> None:
        self._lock = Lock()
        self.requests = 0
        self.cache_hits = 0
        self.rate_limited = 0
        self.by_model: Dict[str, _ModelStat] = {}

    def record(self, model: str, usage: Usage, cost: float, cached: bool,
               latency_ms: float = 0.0) -> None:
        # latency_ms is accepted for interface-parity with SqlUsageStore, which
        # persists it per row; the in-memory snapshot doesn't aggregate it.
        with self._lock:
            self.requests += 1
            if cached:
                self.cache_hits += 1
            s = self.by_model.setdefault(model, _ModelStat())
            s.requests += 1
            s.prompt_tokens += usage.prompt_tokens
            s.completion_tokens += usage.completion_tokens
            s.cost_usd = round(s.cost_usd + cost, 6)

    def record_rate_limited(self) -> None:
        with self._lock:
            self.rate_limited += 1

    def snapshot(self) -> MetricsResponse:
        with self._lock:
            total_tokens = sum(
                s.prompt_tokens + s.completion_tokens for s in self.by_model.values()
            )
            total_cost = round(sum(s.cost_usd for s in self.by_model.values()), 6)
            hit_rate = round(self.cache_hits / self.requests, 4) if self.requests else 0.0
            return MetricsResponse(
                requests=self.requests,
                cache_hits=self.cache_hits,
                cache_hit_rate=hit_rate,
                rate_limited=self.rate_limited,
                total_tokens=total_tokens,
                total_cost_usd=total_cost,
                by_model={
                    m: ModelUsage(
                        requests=s.requests,
                        prompt_tokens=s.prompt_tokens,
                        completion_tokens=s.completion_tokens,
                        cost_usd=s.cost_usd,
                    )
                    for m, s in self.by_model.items()
                },
            )


# --------------------------------------------------------------------------- #
# App factory
# --------------------------------------------------------------------------- #
def _make_metrics_store(cfg: Config):
    """In-memory by default; a SQLAlchemy-backed table when a database is
    configured. The import is lazy so the default path — and the browser demo,
    whose wheel ships without SQLAlchemy — never loads an ORM."""
    if cfg.database_url:
        from .store import SqlUsageStore
        return SqlUsageStore(cfg.database_url)
    return Metrics()


def create_app(config: Config | None = None) -> FastAPI:
    cfg = config or Config.from_env()
    app = FastAPI(
        title="llm-gateway",
        version=__version__,
        summary="A multi-provider LLM gateway: auth, rate limiting, caching, "
        "retries, and per-model cost accounting behind one OpenAI-shaped API.",
    )
    app.state.config = cfg
    app.state.router = Router()
    app.state.cache = ResponseCache(cfg.cache_size)
    app.state.limiter = RateLimiter(cfg.rate_capacity, cfg.rate_refill_per_sec)
    app.state.metrics = _make_metrics_store(cfg)

    def require_api_key(authorization: str = Header(default="")) -> str:
        token = authorization.removeprefix("Bearer ").strip()
        if token not in cfg.api_keys:
            raise HTTPException(status_code=401, detail="invalid or missing API key")
        return token

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        req_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        start = time.perf_counter()
        response = await call_next(request)
        elapsed = (time.perf_counter() - start) * 1000
        response.headers["x-request-id"] = req_id
        logger.info(
            json.dumps(
                {
                    "request_id": req_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "latency_ms": round(elapsed, 2),
                }
            )
        )
        return response

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(version=__version__)

    @app.get("/v1/models", response_model=List[ModelInfo])
    def list_models() -> List[ModelInfo]:
        router: Router = app.state.router
        out: List[ModelInfo] = []
        for model, (p, c) in PRICING.items():
            out.append(
                ModelInfo(
                    id=model,
                    provider=router.provider_name(model),
                    prompt_per_1k_usd=p,
                    completion_per_1k_usd=c,
                )
            )
        return out

    @app.get("/metrics", response_model=MetricsResponse)
    def metrics() -> MetricsResponse:
        return app.state.metrics.snapshot()

    @app.post("/v1/chat/completions")
    def chat_completions(req: ChatRequest, api_key: str = Depends(require_api_key)):
        limiter: RateLimiter = app.state.limiter
        metrics_store: Metrics = app.state.metrics
        router: Router = app.state.router
        cache: ResponseCache = app.state.cache

        if not limiter.allow(api_key):
            metrics_store.record_rate_limited()
            wait = limiter.retry_after(api_key)
            # Retry-After must be an integer number of seconds (RFC 7231).
            retry_after = "3600" if wait == float("inf") else str(max(1, round(wait)))
            raise HTTPException(
                status_code=429,
                detail="rate limit exceeded",
                headers={"Retry-After": retry_after},
            )

        provider = router.provider_for(req.model)

        if req.stream:
            return _stream_response(req, provider, metrics_store)

        cacheable = req.temperature == 0.0
        key = (
            cache_key(req.model, req.messages, req.max_tokens, tenant_id(api_key))
            if cacheable
            else ""
        )
        if cacheable:
            hit = cache.get(key)
            if hit is not None:
                cached = hit.model_copy(update={"cached": True, "latency_ms": 0.0})
                metrics_store.record(req.model, cached.usage, 0.0, cached=True)
                return cached

        start = time.perf_counter()
        try:
            content, finish = with_retry(
                lambda: provider.complete(
                    req.model, req.messages, req.temperature, req.max_tokens
                ),
                retries=cfg.retries,
                base_delay=0.0,  # no real sleeping needed for the mock
            )
        except Exception as exc:  # provider exhausted retries
            raise HTTPException(status_code=502, detail=f"provider error: {exc}") from exc
        latency = (time.perf_counter() - start) * 1000

        usage = Usage(
            prompt_tokens=messages_tokens(req.messages),
            completion_tokens=messages_tokens([Message(role="assistant", content=content)]),
            total_tokens=0,
        )
        usage.total_tokens = usage.prompt_tokens + usage.completion_tokens
        cost = cost_usd(req.model, usage)

        resp = ChatResponse(
            id=f"chatcmpl-{uuid.uuid4().hex[:16]}",
            model=req.model,
            provider=provider.name,
            choices=[
                Choice(
                    index=0,
                    message=Message(role="assistant", content=content),
                    finish_reason=finish,
                )
            ],
            usage=usage,
            cost_usd=cost,
            cached=False,
            latency_ms=round(latency, 2),
        )
        if cacheable:
            cache.set(key, resp)
        metrics_store.record(req.model, usage, cost, cached=False, latency_ms=round(latency, 2))
        return resp

    return app


def _stream_response(req: ChatRequest, provider, metrics_store: Metrics) -> StreamingResponse:
    def gen() -> Iterator[str]:
        chunks: List[str] = []
        for piece in provider.stream(
            req.model, req.messages, req.temperature, req.max_tokens
        ):
            chunks.append(piece)
            yield f"data: {json.dumps({'delta': piece})}\n\n"
        content = "".join(chunks)
        usage = Usage(
            prompt_tokens=messages_tokens(req.messages),
            completion_tokens=messages_tokens(
                [Message(role="assistant", content=content)]
            ),
            total_tokens=0,
        )
        usage.total_tokens = usage.prompt_tokens + usage.completion_tokens
        metrics_store.record(req.model, usage, cost_usd(req.model, usage), cached=False)
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


# Module-level app for ``uvicorn llmgateway.app:app``.
app = create_app()
