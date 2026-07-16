"""A tiny LRU response cache keyed on the semantic request.

Only deterministic requests (``temperature == 0``) are cacheable — caching a
sampled response would be wrong. Keying on a hash of (model, messages,
max_tokens) means identical prompts skip the provider entirely, which is the
single biggest cost/latency win a gateway offers.
"""
from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from typing import List, Optional

from .schemas import ChatResponse, Message


def cache_key(model: str, messages: List[Message], max_tokens: Optional[int]) -> str:
    payload = {
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
