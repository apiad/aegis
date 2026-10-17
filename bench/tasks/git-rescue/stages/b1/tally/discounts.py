"""Discount codes. A code takes a fraction off the subtotal, before tax."""

from decimal import Decimal

CODES = {"WELCOME10": Decimal("0.10")}


def discount_rate(code: str) -> Decimal:
    try:
        return CODES[code.upper()]
    except KeyError:
        raise ValueError(f"unknown discount code: {code}") from None


def apply_discount(amount: Decimal, code: str) -> Decimal:
    return amount * (1 - discount_rate(code))
