"""Cost math for aegis usage aggregation.

segment_cost handles claude-code's cumulative-with-resets ``cost_usd``:
each resume restarts the running total, so a session log holds several
monotonic segments and the true cost is the sum of each segment's final
value. token_cost prices a single per-turn usage dict, splitting new
generation from context-replay (cache reads).
"""
from __future__ import annotations

from decimal import Decimal

from aegis.models import ProviderPrices

_M = Decimal(1_000_000)


def segment_cost(costs: list[Decimal]) -> Decimal:
    total = Decimal(0)
    prev: Decimal | None = None
    for x in costs:
        if prev is not None and x < prev:  # reset → close previous segment
            total += prev
        prev = x
    if prev is not None:
        total += prev
    return total


def token_cost(usage: dict, prices: ProviderPrices) -> tuple[Decimal, Decimal]:
    gen = (Decimal(usage.get("input", 0)) * prices.input
           + Decimal(usage.get("cache_creation", 0)) * prices.cache_write
           + Decimal(usage.get("output", 0)) * prices.output) / _M
    rep = Decimal(usage.get("cache_read", 0)) * prices.cache_hit / _M
    return gen, rep
