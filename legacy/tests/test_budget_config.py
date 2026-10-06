from decimal import Decimal
from pathlib import Path

import pytest

from aegis.budget.budgets import Budget, BudgetConfigError, parse_budgets


def test_parse_single_usd_budget():
    b = parse_budgets([{"usd": 1.00, "window": "1h"}])
    assert len(b) == 1
    assert b[0].constraint == "usd"
    assert b[0].limit == Decimal("1.00")
    assert b[0].window_str == "1h"


def test_parse_output_tokens_budget():
    b = parse_budgets([{"output_tokens": 500_000, "window": "1h"}])
    assert b[0].constraint == "output_tokens"
    assert b[0].limit == Decimal("500000")


def test_parse_multiple_preserves_order():
    b = parse_budgets([
        {"usd": 1.00, "window": "1h"},
        {"usd": 10.00, "window": "24h"},
        {"output_tokens": 500_000, "window": "1h"},
    ])
    assert [x.window_str for x in b] == ["1h", "24h", "1h"]


def test_parse_rejects_both_constraints():
    with pytest.raises(BudgetConfigError, match="exactly one"):
        parse_budgets([{"usd": 1.00, "output_tokens": 500, "window": "1h"}])


def test_parse_rejects_neither_constraint():
    with pytest.raises(BudgetConfigError, match="exactly one"):
        parse_budgets([{"window": "1h"}])


def test_parse_rejects_missing_window():
    with pytest.raises(BudgetConfigError, match="window"):
        parse_budgets([{"usd": 1.00}])


def test_parse_rejects_bad_window():
    with pytest.raises(BudgetConfigError, match="window"):
        parse_budgets([{"usd": 1.00, "window": "5y"}])


def test_parse_rejects_duplicate_pair():
    with pytest.raises(BudgetConfigError, match="duplicate"):
        parse_budgets([
            {"usd": 1.00, "window": "1h"},
            {"usd": 2.00, "window": "1h"},
        ])


def test_parse_rejects_non_positive():
    with pytest.raises(BudgetConfigError, match="positive"):
        parse_budgets([{"usd": 0, "window": "1h"}])
    with pytest.raises(BudgetConfigError, match="positive"):
        parse_budgets([{"output_tokens": -1, "window": "1h"}])


def test_parse_empty_list_returns_empty():
    assert parse_budgets([]) == []
    assert parse_budgets(None) == []


def test_load_queues_attaches_budgets(tmp_path):
    """End-to-end: budgets land on Queue.budgets via load_queues."""
    from aegis.config import load_queues
    (tmp_path / ".aegis.yaml").write_text("""
default_agent: opus
agents:
  opus:
    provider: claude-code
    model: opus
queues:
  impl:
    agent: opus
    max_parallel: 2
    budgets:
      - usd: 1.00
        window: 1h
      - output_tokens: 500000
        window: 1h
""")
    queues = load_queues(tmp_path)
    assert len(queues["impl"].budgets) == 2
    assert queues["impl"].budgets[0].constraint == "usd"


def test_load_queues_with_bad_budget_fails(tmp_path):
    from aegis.config import ConfigError, load_queues
    (tmp_path / ".aegis.yaml").write_text("""
default_agent: opus
agents:
  opus:
    provider: claude-code
    model: opus
queues:
  impl:
    agent: opus
    max_parallel: 1
    budgets:
      - usd: 1.00
        output_tokens: 500
        window: 1h
""")
    with pytest.raises((BudgetConfigError, ConfigError), match="impl"):
        load_queues(tmp_path)
