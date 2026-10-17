import csv
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from .models import Transaction

DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y")


def parse_amount(text: str) -> Decimal:
    """Bank exports write 1,234.50 for income and (1,234.50) for spending."""
    text = text.strip().replace(",", "")
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    return Decimal(text)


def parse_date(text: str):
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognised date: {text!r}")


def read_csv(path: Path, account: str) -> list[Transaction]:
    out = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            out.append(
                Transaction(
                    day=parse_date(row["date"]),
                    account=account,
                    payee=row["payee"].strip(),
                    amount=parse_amount(row["amount"]),
                    category=(row.get("category") or "uncategorized").strip() or "uncategorized",
                )
            )
    return out
