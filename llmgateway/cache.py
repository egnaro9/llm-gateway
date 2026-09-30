"""A tiny LRU response cache keyed on the semantic request, per caller.

Only deterministic requests (``temperature == 0``) are cacheable: caching a
sampled response would be wrong. Keying on a hash of (model, messages,
max_tokens) means identical prompts skip the provider entirely, which is the
single biggest cost/latency win a gateway offers.

The key is also partitioned by caller. One process serves every configured
API key from one cache instance, so a key covering only the request would let
any tenant read another tenant's entry by sending the same prompt. The
response body is not itself the leak, since at ``temperature == 0`` it is a
deterministic function of the request the second tenant supplied. What leaks
is that someone else sent that exact prompt, which over a guessable prompt
space is a usage oracle on another tenant, and the provider cost of the entry,
which the first tenant paid and the second does not. Both contradict a gateway
whose reason to exist is per-tenant accounting.

The partition is a digest, never the raw credential, so the secret does not
enter the hashed payload.
"""
from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from typing import List, Optional

from .schemas import ChatResponse, Message


def tenant_id(api_key: str) -> str:
    """A stable, non-reversing handle for a caller, for use as a cache partition."""
    return hashlib.sha256(b"llm-gateway/tenant/v1:" + api_key.encode("utf-8")).hexdigest()


def cache_key(
    model: str,
    messages: List[Message],
    max_tokens: Optional[int],
    tenant: str,
) -> str:
    payload = {
        "tenant": tenant,
        "model": model,
        "messages": [m.model_dump() for m in messages],
        "max_tokens": max_tokens,
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class ResponseCache:
    def __init__(self, maxsize: int = 512) -> None:
        self.maxsize = maxsize
        self._data: "OrderedDict[str, ChatResponse]" = OrderedDict()

    def get(self, key: str) -> Optional[ChatResponse]:
        if key not in self._data:
            return None
        self._data.move_to_end(key)  # mark most-recently-used
        return self._data[key]

    def set(self, key: str, value: ChatResponse) -> None:
        self._data[key] = value
        self._data.move_to_end(key)
        while len(self._data) > self.maxsize:
            self._data.popitem(last=False)  # evict least-recently-used

    def __len__(self) -> int:
        return len(self._data)
