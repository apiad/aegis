from decimal import ROUND_HALF_EVEN, Decimal

CENT = Decimal("0.01")


def to_cents(x: Decimal) -> Decimal:
    """Round to whole cents, half to even, as the bank does."""
    return x.quantize(CENT, rounding=ROUND_HALF_EVEN)
