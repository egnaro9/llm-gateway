"""Per-API-key token-bucket rate limiting.

In-memory and process-local — fine for a single instance or a demo. The clock
is injectable so tests are deterministic (no ``sleep``). To scale horizontally
you'd back the buckets with Redis; the interface would not change.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Dict


@dataclass
class _Bucket:
    tokens: float
    updated: float


class RateLimiter:
    def __init__(
        self,
        capacity: int = 60,
        refill_per_sec: float = 1.0,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.capacity = capacity
        self.refill_per_sec = refill_per_sec
        self._now = now
        self._buckets: Dict[str, _Bucket] = {}

    def allow(self, key: str, cost: float = 1.0) -> bool:
        now = self._now()
        b = self._buckets.get(key)
        if b is None:
            b = _Bucket(tokens=float(self.capacity), updated=now)
            self._buckets[key] = b
        # Refill based on elapsed time, capped at capacity.
        elapsed = now - b.updated
        b.tokens = min(self.capacity, b.tokens + elapsed * self.refill_per_sec)
        b.updated = now
        if b.tokens >= cost:
            b.tokens -= cost
            return True
        return False

    def retry_after(self, key: str, cost: float = 1.0) -> float:
        b = self._buckets.get(key)
        if b is None or b.tokens >= cost:
            return 0.0
        if self.refill_per_sec <= 0:
            return float("inf")  # never refills
        return (cost - b.tokens) / self.refill_per_sec
