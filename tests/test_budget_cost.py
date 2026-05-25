from dataclasses import dataclass
from decimal import Decimal

import pytest

from aegis.budget.cost import Cost, compute
from aegis.budget.prices import UnknownPriceError


@dataclass
class _FakeMetrics:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_write_tokens: int = 0
    thinking_tokens: int = 0


def test_compute_sums_all_token_classes_for_opus():
    m = _FakeMetrics(input_tokens=10_000, output_tokens=5_000,
                    cache_hit_tokens=100_000, cache_write_tokens=2_000,
                    thinking_tokens=1_000)
    c = compute(m, "claude-code", "opus")
    # opus rates per million: in=15, out=75, hit=1.50, write=18.75, think=75
    # = 10_000*15/1M + 5_000*75/1M + 100_000*1.5/1M + 2_000*18.75/1M + 1_000*75/1M
    # = 0.15 + 0.375 + 0.15 + 0.0375 + 0.075 = 0.7875
    assert c.usd == Decimal("0.7875")
    assert c.input_tokens == 10_000
    assert c.output_tokens == 5_000
    assert c.cache_hit_tokens == 100_000
    assert c.cache_write_tokens == 2_000
    assert c.thinking_tokens == 1_000


def test_compute_zero_metrics_is_zero_cost():
    c = compute(_FakeMetrics(), "claude-code", "haiku")
    assert c.usd == Decimal("0")
    assert c.output_tokens == 0


def test_compute_missing_attr_defaults_to_zero():
    """Defensive: ACP-driven providers may not split cache classes."""
    class _Sparse:
        input_tokens = 1_000_000
        output_tokens = 1_000_000
        # cache_hit_tokens / cache_write_tokens / thinking_tokens absent
    c = compute(_Sparse(), "claude-code", "haiku")
    # haiku: in=1.00/M, out=5.00/M → 1.00 + 5.00 = 6.00
    assert c.usd == Decimal("6.00")


def test_compute_unknown_model_raises():
    with pytest.raises(UnknownPriceError):
        compute(_FakeMetrics(input_tokens=1), "claude-code", "ghost")


def test_cost_as_dict_serializes_decimal_as_string():
    c = Cost(usd=Decimal("0.0421"), input_tokens=1, output_tokens=2,
              cache_hit_tokens=3, cache_write_tokens=4, thinking_tokens=5)
    d = c.as_dict()
    assert d["usd"] == "0.0421"
    assert d["input_tokens"] == 1


def test_cost_from_dict_round_trips():
    """JSONL round-trip: dict -> Cost -> dict must be identical."""
    src = {"usd": "0.0421", "input_tokens": 1, "output_tokens": 2,
           "cache_hit_tokens": 3, "cache_write_tokens": 4,
           "thinking_tokens": 5}
    c = Cost.from_dict(src)
    assert c.usd == Decimal("0.0421")
    assert c.as_dict() == src


def test_compute_no_float_drift_over_1000_rounds():
    """1000 small computes summed equal 1000 * single compute."""
    m = _FakeMetrics(input_tokens=12_345, output_tokens=6_789)
    total = sum((compute(m, "claude-code", "sonnet").usd for _ in range(1000)),
                start=Decimal("0"))
    single = compute(m, "claude-code", "sonnet").usd
    assert total == single * 1000
