from datetime import date
from decimal import Decimal

from ledger.parse import parse_amount, parse_date, read_csv


def test_income_amount():
    assert parse_amount("1,234.50") == Decimal("1234.50")


def test_spending_amount_is_negative():
    assert parse_amount("(1,234.50)") == Decimal("-1234.50")


def test_both_date_formats():
    assert parse_date("2026-01-05") == parse_date("05/01/2026") == date(2026, 1, 5)


def test_read_csv_nets_income_against_spending(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text('date,payee,amount,category\n2026-02-01,Employer,"1,000.00",salary\n2026-02-02,Shop,(250.00),\n')
    txs = read_csv(p, "main")
    assert sum(t.amount for t in txs) == Decimal("750.00")
    assert txs[1].category == "uncategorized"
