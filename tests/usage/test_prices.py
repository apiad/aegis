"""The price table: lookups and the rates that differ from the usual multiples.

Rates from https://platform.claude.com/docs/en/about-claude/pricing, 2026-10-08.
"""

from decimal import Decimal

import pytest

from aegis.usage.prices import PRICES, prices_for


def test_a_dated_id_and_a_context_suffix_price_as_the_base_model():
    base = prices_for("claude-haiku-4-5")
    assert base is not None
    assert prices_for("claude-haiku-4-5-20251001") == base
    assert prices_for("claude-opus-4-6[1m]") == prices_for("claude-opus-4-6")


def test_a_bare_alias_prices_as_its_family():
    assert prices_for("opus") == prices_for("claude-opus-5")
    assert prices_for("sonnet") == prices_for("claude-sonnet-4-6")


def test_an_unknown_model_has_no_price():
    assert prices_for("claude-newest-9") is None
    assert prices_for("opencode-go/deepseek-v4-pro") is None
    assert prices_for(None) is None


def test_a_synthetic_message_costs_nothing():
    p = prices_for("<synthetic>")
    assert p is not None
    assert p.cost(inp=10**6, out=10**6, cc5=10**6, cc1=10**6, cache_read=10**6) == 0


@pytest.mark.parametrize("model", sorted(PRICES))
def test_cache_writes_follow_the_input_price(model):
    """A 5-minute write is 1.25x input and a 1-hour write 2x input, for every
    model. The legacy engine priced 1-hour writes at 2x the 5-minute rate,
    2.5x input, a quarter too much."""
    p = PRICES[model]
    assert p.cache_write_5m == p.input * Decimal("1.25")
    assert p.cache_write_1h == p.input * 2


@pytest.mark.parametrize(
    "model, multiple",
    [
        ("claude-fable-5-1", "0.025"),
        ("claude-opus-5-5", "0.05"),
        ("claude-sonnet-5-5", "0.05"),
        ("claude-fable-5", "0.1"),
        ("claude-opus-5", "0.1"),
        ("claude-sonnet-5", "0.1"),
        ("claude-haiku-4-5", "0.1"),
    ],
)
def test_cache_reads_take_each_models_own_multiple(model, multiple):
    p = PRICES[model]
    assert p.cache_read == p.input * Decimal(multiple)
