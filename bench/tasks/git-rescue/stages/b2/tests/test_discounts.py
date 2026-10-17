from decimal import Decimal as D

import pytest

from tally.discounts import apply_discount
from tally.invoice import invoice_total


def test_welcome_takes_ten_percent():
    assert apply_discount(D("50.00"), "welcome10") == D("45.00")


def test_unknown_code_is_refused():
    with pytest.raises(ValueError):
        apply_discount(D("50.00"), "FREE")


def test_discount_comes_before_tax():
    assert invoice_total([(2, D("10.00"))], D("0.21"), code="WELCOME10") == D("21.78")


def test_bulk_needs_ten_items():
    assert invoice_total([(10, D("1.00"))], code="BULK5") == D("9.50")
    with pytest.raises(ValueError):
        invoice_total([(9, D("1.00"))], code="BULK5")
