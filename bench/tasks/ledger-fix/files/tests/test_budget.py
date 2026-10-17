from datetime import date
from decimal import Decimal

from ledger.budget import check, over_budget
from ledger.models import Transaction


def tx(day, amount, category):
    return Transaction(day, "main", "payee", Decimal(amount), category)


def test_under_the_limit_is_fine():
    lines = check([tx(date(2026, 1, 4), "-80", "food")], {"food": Decimal("200")}, "2026-01")
    assert over_budget(lines) == [] and lines[0].left == Decimal("120")


def test_spending_at_the_limit_is_flagged():
    txs = [tx(date(2026, 1, 4), "-120", "food"), tx(date(2026, 1, 20), "-80", "food"), tx(date(2026, 2, 1), "-500", "food")]
    assert over_budget(check(txs, {"food": Decimal("200"), "fun": Decimal("10")}, "2026-01")) == ["food"]
