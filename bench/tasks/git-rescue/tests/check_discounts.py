from decimal import Decimal as D

import pytest

from tally.discounts import apply_discount
from tally.invoice import Line, invoice_total


def test_welcome_takes_ten_percent():
    assert apply_discount(D("50.00"), "welcome10") == D("45.00")


def test_unknown_code_is_refused():
    with pytest.raises(ValueError):
        apply_discount(D("50.00"), "FREE")


def test_discount_comes_before_tax():
    assert invoice_total([Line("pad", 2, D("10.00"))], D("0.21"), code="WELCOME10") == D("21.78")


def test_bulk_needs_ten_items():
    assert invoice_total([Line("pen", 4, D("1.00")), Line("pad", 6, D("1.00"))], code="BULK5") == D("9.50")
    with pytest.raises(ValueError):
        invoice_total([Line("pen", 9, D("1.00"))], code="BULK5")


def test_lines_still_round_with_a_code():
    assert invoice_total([Line("tack", 1, D("0.005")), Line("pin", 1, D("0.005"))], code="WELCOME10") == D("0.00")
