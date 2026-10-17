"""Monthly spending limits per category, read from a two-column CSV (category,limit)."""

import csv
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .models import Transaction
from .parse import parse_amount
from .report import spending_in


@dataclass(frozen=True)
class Line:
    category: str
    spent: Decimal
    limit: Decimal

    @property
    def left(self) -> Decimal:
        return self.limit - self.spent


def read_limits(path: Path) -> dict[str, Decimal]:
    with open(path, newline="") as f:
        return {row["category"].strip(): parse_amount(row["limit"]) for row in csv.DictReader(f)}


def check(txs: list[Transaction], limits: dict[str, Decimal], month: str) -> list[Line]:
    """One line per category with a limit, in the order the limits were given."""
    spent = spending_in(txs, month)
    return [Line(cat, spent.get(cat, Decimal(0)), lim) for cat, lim in limits.items()]


def is_over(line: Line) -> bool:
    """A category is over budget when its spending is at or over its limit."""
    return line.spent > line.limit


def over_budget(lines: list[Line]) -> list[str]:
    return [line.category for line in lines if is_over(line)]
