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
    row = lookup("claude-code", "opus")
    assert row.input == Decimal("15.00")
    assert row.output == Decimal("75.00")


def test_lookup_unknown_pair_raises():
    with pytest.raises(UnknownPriceError, match="no price for"):
        lookup("madeup-provider", "made-up-model")
