"""Invoice arithmetic. Prices are Decimals."""

from dataclasses import dataclass
from decimal import Decimal

from .money import to_cents


@dataclass(frozen=True)
class Line:
    sku: str
    qty: int
    unit: Decimal


def line_total(line: Line) -> Decimal:
    return line.qty * line.unit


def subtotal(lines: list[Line]) -> Decimal:
    return sum((line_total(x) for x in lines), Decimal(0))


def tax(amount: Decimal, rate: Decimal) -> Decimal:
    return amount * rate


def invoice_total(lines: list[Line], rate: Decimal = Decimal("0")) -> Decimal:
    s = subtotal(lines)
    return to_cents(s + tax(s, rate))
