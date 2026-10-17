"""Invoice arithmetic. A line is a (quantity, unit price) tuple; prices are Decimals."""

from decimal import Decimal

from .discounts import apply_discount
from .money import to_cents


def line_total(line) -> Decimal:
    qty, unit = line
    return qty * unit


def subtotal(lines) -> Decimal:
    return sum((line_total(x) for x in lines), Decimal(0))


def tax(amount: Decimal, rate: Decimal) -> Decimal:
    return amount * rate


def invoice_total(lines, rate: Decimal = Decimal("0"), code: str | None = None) -> Decimal:
    s = subtotal(lines)
    if code:
        s = apply_discount(s, code)
    return to_cents(s + tax(s, rate))
