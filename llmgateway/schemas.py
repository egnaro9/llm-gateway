"""Pydantic request/response models — the gateway's typed contract.

These double as validation (bad input -> 422 automatically) and as the source
of the OpenAPI schema served at ``/docs``. The shapes intentionally mirror the
OpenAI chat-completions API so existing clients feel at home.
"""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

Role = Literal["system", "user", "assistant"]


class Message(BaseModel):
    role: Role
    content: str = Field(..., min_length=1, max_length=100_000)


class ChatRequest(BaseModel):
    model: str = Field(..., examples=["mock-1"])
    messages: List[Message] = Field(..., min_length=1)
    temperature: float = Field(0.0, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(None, ge=1, le=32_000)
    stream: bool = False


class Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class Choice(BaseModel):
    index: int
    message: Message
    finish_reason: str


class ChatResponse(BaseModel):
    id: str
    model: str
    provider: str
    choices: List[Choice]
    usage: Usage
    cost_usd: float
    cached: bool = False
    latency_ms: float


class ModelInfo(BaseModel):
    id: str
    provider: str
    prompt_per_1k_usd: float
    completion_per_1k_usd: float


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str


class ModelUsage(BaseModel):
    requests: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float


class MetricsResponse(BaseModel):
    requests: int
    cache_hits: int
    cache_hit_rate: float
    rate_limited: int
    total_tokens: int
    total_cost_usd: float
    by_model: dict[str, ModelUsage]
