from datetime import date
from decimal import Decimal

from ledger.balance import running
from ledger.models import Transaction
from ledger.report import by_category, monthly, months_between


def tx(day, amount, category="misc"):
    return Transaction(day, "main", "payee", Decimal(amount), category)


def test_months_cross_a_year():
    assert months_between(date(2025, 11, 3), date(2026, 2, 1)) == ["2025-11", "2025-12", "2026-01", "2026-02"]


def test_monthly_keeps_december():
    txs = [tx(date(2025, 12, 5), "-900"), tx(date(2026, 1, 2), "2000")]
    assert [r["month"] for r in monthly(txs)] == ["2025-12", "2026-01"]


def test_monthly_net_within_one_month():
    rows = monthly([tx(date(2026, 3, 1), "1000"), tx(date(2026, 3, 9), "-250.50")])
    assert rows == [{"month": "2026-03", "income": Decimal("1000"), "spending": Decimal("250.50"), "net": Decimal("749.50")}]


def test_by_category_largest_first():
    txs = [tx(date(2026, 3, 1), "-5", "fun"), tx(date(2026, 3, 2), "-40", "food"), tx(date(2026, 3, 3), "-10", "fun")]
    assert list(by_category(txs).items()) == [("food", Decimal("40")), ("fun", Decimal("15"))]


def test_by_category_since_counts_that_day():
    txs = [tx(date(2026, 3, 1), "-5", "fun"), tx(date(2026, 3, 2), "-40", "food")]
    assert by_category(txs, since=date(2026, 3, 2)) == {"food": Decimal("40")}


def test_running_balance_is_oldest_first():
    txs = [tx(date(2026, 3, 9), "-30"), tx(date(2026, 3, 1), "100")]
    assert [b for _, b in running(txs, "main")] == [Decimal("100"), Decimal("70")]
