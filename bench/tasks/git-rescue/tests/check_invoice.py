from decimal import Decimal as D

from tally.invoice import Line, invoice_total, line_total, subtotal


def test_line_total():
    assert line_total(Line("pen", 3, D("2.50"))) == D("7.50")


def test_subtotal():
    assert subtotal([Line("pad", 1, D("10")), Line("clip", 2, D("0.25"))]) == D("10.50")


def test_invoice_total_adds_tax():
    assert invoice_total([Line("pad", 2, D("10.00"))], D("0.21")) == D("24.20")


def test_lines_are_values():
    assert Line("pen", 1, D("1")) == Line("pen", 1, D("1"))


def test_each_line_rounds_to_cents():
    assert line_total(Line("tack", 3, D("0.333"))) == D("1.00")
