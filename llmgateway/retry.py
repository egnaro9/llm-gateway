"""Exponential-backoff retry for transient provider errors.

The sleep function is injectable so tests run instantly (``sleep=lambda _: None``)
while production uses real backoff with jitter-free, capped delays.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Tuple, Type, TypeVar

T = TypeVar("T")


@dataclass
class RetryStats:
    attempts: int = 0


def with_retry(
    fn: Callable[[], T],
    retries: int = 3,
    base_delay: float = 0.05,
    max_delay: float = 2.0,
    exceptions: Tuple[Type[BaseException], ...] = (Exception,),
    sleep: Callable[[float], None] = time.sleep,
    stats: RetryStats | None = None,
) -> T:
    """Call ``fn`` up to ``retries`` times, backing off 2^n * base_delay."""
    last: BaseException | None = None
    for attempt in range(retries):
        if stats is not None:
            stats.attempts = attempt + 1
        try:
            return fn()
        except exceptions as exc:  # noqa: PERF203
            last = exc
            if attempt == retries - 1:
                break
            sleep(min(max_delay, base_delay * (2**attempt)))
    assert last is not None
    raise last
