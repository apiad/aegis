"""Cost math for aegis usage aggregation.

segment_cost handles claude-code's cumulative-with-resets ``cost_usd``:
each resume restarts the running total, so a session log holds several
monotonic segments and the true cost is the sum of each segment's final
value. token_cost prices a single per-turn usage dict, splitting new
generation from context replay (cache reads).
"""

from __future__ import annotations

from decimal import Decimal

from .prices import Prices


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


def token_cost(usage: dict, prices: Prices) -> tuple[Decimal, Decimal]:
    """(new generation, context replay) for one turn's tokens: input, output,
    and cache writes ``cc5``/``cc1`` against cache reads."""
    gen = prices.cost(
        inp=usage.get("input", 0),
        out=usage.get("output", 0),
        cc5=usage.get("cc5", 0),
        cc1=usage.get("cc1", 0),
        cache_read=0,
    )
    rep = prices.cost(inp=0, out=0, cc5=0, cc1=0, cache_read=usage.get("cache_read", 0))
    return gen, rep
