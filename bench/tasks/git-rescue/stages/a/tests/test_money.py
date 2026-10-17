from decimal import Decimal as D

from tally.money import to_cents


def test_half_rounds_to_even():
    assert to_cents(D("0.125")) == D("0.12")
    assert to_cents(D("0.135")) == D("0.14")


def test_whole_cents_stay():
    assert to_cents(D("3.10")) == D("3.10")
