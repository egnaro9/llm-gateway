"""A small model-pricing table + cost calculation.

Prices are USD per 1K tokens and are illustrative (they change often — treat
this as the place you'd wire a real price feed). The mock models are free, so
the whole gateway runs and is billed-accounted end-to-end with no real spend.
"""
from __future__ import annotations

from typing import Dict, Tuple

from .schemas import Usage

# model id -> (prompt $/1k, completion $/1k)
PRICING: Dict[str, Tuple[float, float]] = {
    "mock-1": (0.0, 0.0),
    "mock-flaky": (0.0, 0.0),
    # A priced mock so cost accounting is demonstrable fully offline.
    "mock-pro": (0.00100, 0.00200),
    "gpt-4o-mini": (0.00015, 0.00060),
    "gpt-4o": (0.00250, 0.01000),
    "claude-haiku-4-5": (0.00080, 0.00400),
    "claude-sonnet-5": (0.00300, 0.01500),
}


def price_for(model: str) -> Tuple[float, float]:
    return PRICING.get(model, (0.0, 0.0))


def cost_usd(model: str, usage: Usage) -> float:
    p_per_1k, c_per_1k = price_for(model)
    cost = (usage.prompt_tokens / 1000.0) * p_per_1k + (
        usage.completion_tokens / 1000.0
    ) * c_per_1k
    # Round to 6 decimals — sub-cent costs matter when you aggregate millions.
    return round(cost, 6)
