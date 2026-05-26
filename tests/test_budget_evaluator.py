from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from aegis.budget.budgets import Budget
from aegis.budget.evaluator import BudgetCheck, Decision, evaluate_budgets
from aegis.budget.windows import parse_window


def _now() -> datetime:
    return datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc)


def _budget(constraint: str, limit: str, window_str: str) -> Budget:
    return Budget(constraint=constraint, limit=Decimal(limit),
                  window_str=window_str, window=parse_window(window_str))


def _rec(ts: datetime, event: str = "completed",
         usd: str = "0", output_tokens: int = 0) -> dict:
    return {
        "event": event,
        "completed_at": ts.isoformat().replace("+00:00", "Z"),
        "cost": {"usd": usd, "input_tokens": 0,
                  "output_tokens": output_tokens,
                  "cache_hit_tokens": 0, "cache_write_tokens": 0,
                  "thinking_tokens": 0},
    }


def test_no_budgets_allows():
    d = evaluate_budgets([], [], _now())
    assert d.allowed is True
    assert d.blocked_by == []


def test_completed_record_counts():
    n = _now()
    tail = [_rec(n - timedelta(minutes=10), event="completed", usd="0.50")]
    d = evaluate_budgets(tail, [_budget("usd", "1.00", "1h")], n)
    assert d.allowed is True
    assert d.checks[0].spent == Decimal("0.50")


def test_failed_record_also_counts():
    """Failed workers burned tokens — count them."""
    n = _now()
    tail = [_rec(n - timedelta(minutes=10), event="failed", usd="0.80")]
    d = evaluate_budgets(tail, [_budget("usd", "1.00", "1h")], n)
    assert d.checks[0].spent == Decimal("0.80")


def test_under_limit_allows():
    n = _now()
    tail = [_rec(n - timedelta(minutes=10), usd="0.50")]
    d = evaluate_budgets(tail, [_budget("usd", "1.00", "1h")], n)
    assert d.allowed is True
    assert d.checks[0].headroom == Decimal("0.50")


def test_over_limit_blocks():
    n = _now()
    tail = [_rec(n - timedelta(minutes=10), usd="0.80"),
            _rec(n - timedelta(minutes=20), usd="0.50")]
    d = evaluate_budgets(tail, [_budget("usd", "1.00", "1h")], n)
    assert d.allowed is False
    assert d.blocked_by[0].spent == Decimal("1.30")


def test_records_outside_window_ignored():
    n = _now()
    tail = [_rec(n - timedelta(minutes=30), usd="0.50"),
            _rec(n - timedelta(hours=2), usd="100.00")]
    d = evaluate_budgets(tail, [_budget("usd", "1.00", "1h")], n)
    assert d.allowed is True


def test_multi_budget_partial_block():
    n = _now()
    tail = [_rec(n - timedelta(minutes=10), usd="0.80"),
            _rec(n - timedelta(minutes=20), usd="0.50")]
    d = evaluate_budgets(tail, [
        _budget("usd", "1.00", "1h"),    # blocks
        _budget("usd", "10.00", "24h"),  # ok
    ], n)
    assert d.allowed is False
    assert len(d.blocked_by) == 1
    assert d.blocked_by[0].window_str == "1h"


def test_output_tokens_budget():
    n = _now()
    tail = [_rec(n - timedelta(minutes=5), output_tokens=600_000)]
    d = evaluate_budgets(tail, [_budget("output_tokens", "500000", "1h")], n)
    assert d.allowed is False
    assert d.blocked_by[0].spent == Decimal("600000")


def test_unblock_at_for_blocking_budget():
    n = _now()
    older = n - timedelta(minutes=30)
    newer = n - timedelta(minutes=10)
    tail = [_rec(newer, usd="0.80"), _rec(older, usd="0.50")]
    d = evaluate_budgets(tail, [_budget("usd", "1.00", "1h")], n)
    assert d.allowed is False
    # Older ages out first; remaining 0.80 < 1.00 → allowed.
    assert d.blocked_by[0].unblock_at == older + timedelta(hours=1)


def test_decision_unblock_at_is_max():
    n = _now()
    tail = [_rec(n - timedelta(minutes=10), usd="2.00",
                  output_tokens=600_000)]
    d = evaluate_budgets(tail, [
        _budget("usd", "1.00", "1h"),
        _budget("output_tokens", "500000", "30m"),
    ], n)
    assert d.allowed is False
    times = [c.unblock_at for c in d.blocked_by if c.unblock_at]
    assert d.unblock_at == max(times)


def test_record_without_cost_counts_as_zero():
    """Backwards compat for pre-budget records."""
    n = _now()
    tail = [
        {"event": "completed",
         "completed_at": (n - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")},
        _rec(n - timedelta(minutes=10), usd="0.30"),
    ]
    d = evaluate_budgets(tail, [_budget("usd", "1.00", "1h")], n)
    assert d.allowed is True
    assert d.checks[0].spent == Decimal("0.30")


def test_non_terminal_events_ignored():
    n = _now()
    tail = [
        {"event": "task_enqueued",
         "completed_at": (n - timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
         "cost": {"usd": "100.00"}},
        _rec(n - timedelta(minutes=10), usd="0.30"),
    ]
    d = evaluate_budgets(tail, [_budget("usd", "1.00", "1h")], n)
    assert d.checks[0].spent == Decimal("0.30")
