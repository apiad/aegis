from decimal import Decimal

import pytest

from aegis.budget.prices import PRICES, ProviderPrices, UnknownPriceError, lookup


def test_provider_prices_uses_decimal():
    p = PRICES[("claude-code", "opus")]
    assert isinstance(p.input, Decimal)
    assert isinstance(p.output, Decimal)
    assert isinstance(p.cache_hit, Decimal)
    assert isinstance(p.cache_write, Decimal)
    assert isinstance(p.thinking, Decimal)


def test_lookup_known_pair_returns_row():
    """Claude Opus 4.7 rate card (per Anthropic docs + models.dev):
    $5/$25 input/output per million tokens."""
    row = lookup("claude-code", "opus")
    assert row.input == Decimal("5.00")
    assert row.output == Decimal("25.00")


def test_lookup_unknown_pair_raises():
    with pytest.raises(UnknownPriceError, match="no price for"):
        lookup("madeup-provider", "made-up-model")
