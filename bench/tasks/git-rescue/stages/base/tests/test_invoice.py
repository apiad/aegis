from decimal import Decimal as D

from tally.invoice import invoice_total, line_total, subtotal


def test_line_total():
    assert line_total((3, D("2.50"))) == D("7.50")


def test_subtotal():
    assert subtotal([(1, D("10")), (2, D("0.25"))]) == D("10.50")


def test_invoice_total_adds_tax():
    assert invoice_total([(2, D("10.00"))], D("0.21")) == D("24.20")
