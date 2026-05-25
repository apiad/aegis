"""Static per-(provider, model) price table for cost computation.

Rates are per-MILLION-tokens in USD. Update this file when providers
publish new prices — it is the only piece of maintained data the
budget feature depends on.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


class UnknownPriceError(KeyError):
    """Raised when cost.compute() can't find a (provider, model) pair."""


@dataclass(frozen=True)
class ProviderPrices:
    """Per-million-token rates in USD, all Decimal to avoid float drift."""
    input:       Decimal
    output:      Decimal
    cache_hit:   Decimal
    cache_write: Decimal
    thinking:    Decimal


def _d(s: str) -> Decimal:
    return Decimal(s)


PRICES: dict[tuple[str, str], ProviderPrices] = {
    # Claude Code (Anthropic) — Nov 2025 list prices.
    ("claude-code", "opus"): ProviderPrices(
        input=_d("15.00"), output=_d("75.00"),
        cache_hit=_d("1.50"), cache_write=_d("18.75"),
        thinking=_d("75.00")),
    ("claude-code", "sonnet"): ProviderPrices(
        input=_d("3.00"), output=_d("15.00"),
        cache_hit=_d("0.30"), cache_write=_d("3.75"),
        thinking=_d("15.00")),
    ("claude-code", "haiku"): ProviderPrices(
        input=_d("1.00"), output=_d("5.00"),
        cache_hit=_d("0.10"), cache_write=_d("1.25"),
        thinking=_d("5.00")),
    # Gemini CLI — Nov 2025 list prices.
    ("gemini", "gemini-3-pro"): ProviderPrices(
        input=_d("1.25"), output=_d("10.00"),
        cache_hit=_d("0.31"), cache_write=_d("1.25"),
        thinking=_d("10.00")),
    ("gemini", "gemini-3-flash-preview"): ProviderPrices(
        input=_d("0.075"), output=_d("0.30"),
        cache_hit=_d("0.019"), cache_write=_d("0.075"),
        thinking=_d("0.30")),
    # OpenCode — provider-routed; defaults match Kimi K2.6 listed pricing.
    ("opencode", "kimi-k2.6"): ProviderPrices(
        input=_d("0.30"), output=_d("1.20"),
        cache_hit=_d("0.06"), cache_write=_d("0.30"),
        thinking=_d("1.20")),
}


def lookup(provider: str, model: str) -> ProviderPrices:
    """Return the price row, raise UnknownPriceError on miss."""
    try:
        return PRICES[(provider, model)]
    except KeyError:
        raise UnknownPriceError(
            f"no price for {(provider, model)!r}; "
            f"add to aegis.budget.prices.PRICES")
