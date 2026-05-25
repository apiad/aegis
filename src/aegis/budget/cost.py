"""Compute USD cost from SessionMetrics + price table."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from aegis.budget.prices import lookup

_MILLION = Decimal("1000000")


@dataclass(frozen=True)
class Cost:
    """A worker's finalized cost, ready to land on a task_done JSONL record."""
    usd:                Decimal
    input_tokens:       int
    output_tokens:      int
    cache_hit_tokens:   int
    cache_write_tokens: int
    thinking_tokens:    int

    def as_dict(self) -> dict:
        """Serialize for JSONL — `usd` becomes a string to avoid float drift."""
        return {
            "usd": str(self.usd),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_hit_tokens": self.cache_hit_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "thinking_tokens": self.thinking_tokens,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Cost":
        return cls(
            usd=Decimal(d["usd"]),
            input_tokens=int(d.get("input_tokens", 0)),
            output_tokens=int(d.get("output_tokens", 0)),
            cache_hit_tokens=int(d.get("cache_hit_tokens", 0)),
            cache_write_tokens=int(d.get("cache_write_tokens", 0)),
            thinking_tokens=int(d.get("thinking_tokens", 0)),
        )


def compute(metrics, provider: str, model: str) -> Cost:
    """Compute USD cost for the worker, looking up rates by (provider, model).

    ``metrics`` is any object exposing the SessionMetrics token attributes.
    Missing attributes default to 0 (some providers don't expose all of
    cache_hit/cache_write/thinking).
    Raises UnknownPriceError if (provider, model) isn't in PRICES.
    """
    row = lookup(provider, model)

    def _tok(name: str) -> int:
        return int(getattr(metrics, name, 0) or 0)

    inp = _tok("input_tokens")
    out = _tok("output_tokens")
    hit = _tok("cache_hit_tokens")
    wr  = _tok("cache_write_tokens")
    th  = _tok("thinking_tokens")

    usd = (
        Decimal(inp) * row.input       / _MILLION +
        Decimal(out) * row.output      / _MILLION +
        Decimal(hit) * row.cache_hit   / _MILLION +
        Decimal(wr)  * row.cache_write / _MILLION +
        Decimal(th)  * row.thinking    / _MILLION
    )
    return Cost(usd=usd, input_tokens=inp, output_tokens=out,
                cache_hit_tokens=hit, cache_write_tokens=wr,
                thinking_tokens=th)
