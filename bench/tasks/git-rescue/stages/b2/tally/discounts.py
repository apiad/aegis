"""Discount codes. A code takes a fraction off the subtotal, before tax."""

from decimal import Decimal

CODES = {"WELCOME10": Decimal("0.10"), "BULK5": Decimal("0.05")}
MIN_ITEMS = {"BULK5": 10}


def discount_rate(code: str) -> Decimal:
    try:
        return CODES[code.upper()]
    except KeyError:
        raise ValueError(f"unknown discount code: {code}") from None


def apply_discount(amount: Decimal, code: str) -> Decimal:
    return amount * (1 - discount_rate(code))


def check_eligible(lines, code: str) -> None:
    """Some codes need a minimum number of items on the invoice."""
    items = sum(qty for qty, _ in lines)
    needed = MIN_ITEMS.get(code.upper(), 0)
    if items < needed:
        raise ValueError(f"{code} needs {needed} items, the invoice has {items}")
