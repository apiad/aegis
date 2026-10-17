from collections import defaultdict
from datetime import date
from decimal import Decimal

from .models import Transaction


def month_key(d: date) -> str:
    return f"{d.year}-{d.month:02d}"


def months_between(start: date, end: date) -> list[str]:
    """Every month from start's to end's, inclusive, so empty months still show."""
    keys = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        keys.append(f"{y}-{m:02d}")
        m += 1
        if m == 12:
            y, m = y + 1, 1
    return keys


def monthly(txs: list[Transaction]) -> list[dict]:
    """One row per month: income, spending (as a positive number) and net."""
    if not txs:
        return []
    by = defaultdict(lambda: {"income": Decimal(0), "spending": Decimal(0)})
    for t in txs:
        k = month_key(t.day)
        if t.amount >= 0:
            by[k]["income"] += t.amount
        else:
            by[k]["spending"] += -t.amount
    days = [t.day for t in txs]
    rows = []
    for k in months_between(min(days), max(days)):
        r = by[k]
        rows.append({"month": k, "income": r["income"], "spending": r["spending"], "net": r["income"] - r["spending"]})
    return rows


def spending_in(txs: list[Transaction], month: str) -> dict[str, Decimal]:
    """Spending per category in one month ("YYYY-MM"), as positive numbers."""
    out: dict[str, Decimal] = defaultdict(Decimal)
    for t in txs:
        if t.amount < 0 and month_key(t.day) == month:
            out[t.category] += -t.amount
    return dict(out)


def by_category(txs: list[Transaction], since: date | None = None) -> dict[str, Decimal]:
    """Spending per category, largest first, counting only days on or after `since`."""
    out: dict[str, Decimal] = defaultdict(Decimal)
    for t in txs:
        if since is not None and t.day < since:
            continue
        if t.amount < 0:
            out[t.category] += -t.amount
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))
