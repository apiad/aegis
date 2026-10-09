from decimal import Decimal

from aegis.usage.cost import segment_cost, token_cost
from aegis.usage.prices import Prices


def _c(*xs):
    return [Decimal(str(x)) for x in xs]


def test_segment_cost_monotonic_single_run():
    # one non-decreasing run → cost is its final value
    assert segment_cost(_c(0.6, 0.8, 1.0, 1.37)) == Decimal("1.37")


def test_segment_cost_single_reset():
    # run A ends at 1.0, resume run B ends at 0.5 → 1.5
    assert segment_cost(_c(0.6, 1.0, 0.3, 0.5)) == Decimal("1.5")


def test_segment_cost_multiple_resets():
    # segments end at 1.0, 0.5, 2.0 → 3.5
    assert segment_cost(_c(0.6, 1.0, 0.3, 0.5, 0.1, 2.0)) == Decimal("3.5")


def test_segment_cost_empty_and_single():
    assert segment_cost([]) == Decimal(0)
    assert segment_cost(_c(0.7)) == Decimal("0.7")


PRICES = Prices(
    input=Decimal("5"),
    output=Decimal("25"),
    cache_write_5m=Decimal("6.25"),
    cache_write_1h=Decimal("10"),
    cache_read=Decimal("0.5"),
)


def test_token_cost_splits_generation_from_replay():
    usage = {
        "input": 1_000_000,
        "output": 1_000_000,
        "cc5": 1_000_000,
        "cc1": 1_000_000,
        "cache_read": 1_000_000,
    }
    gen, rep = token_cost(usage, PRICES)
    assert gen == Decimal("46.25")  # 5 + 25 + 6.25 + 10
    assert rep == Decimal("0.5")


def test_token_cost_missing_keys_default_zero():
    gen, rep = token_cost({"output": 2_000_000}, PRICES)
    assert gen == Decimal("50")
    assert rep == Decimal("0")
