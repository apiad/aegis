"""Splitting a subscription's fee and weekly limit over hourly costs (spec 08)."""

from decimal import Decimal

from aegis.usage.subscription import Reading, split

W = 7 * 86400  # the window resets a week on
WEEK_FEE = Decimal(200) * 12 / 52


def close(a, b) -> bool:
    return abs(Decimal(a) - Decimal(b)) < Decimal("1e-9")


def test_a_rise_goes_to_the_groups_that_ran_in_its_interval_by_cost():
    r = [Reading(0, 10, W), Reading(3600, 14, W)]  # +4 points in hour 0
    s = split(
        r, {(0, ("atlas",)): Decimal(3), (0, ("notes",)): Decimal(1)}, Decimal(200)
    )
    assert s.limit == {("atlas",): 3.0, ("notes",): 1.0}
    assert s.kind == "metered" and s.elsewhere == 0 and s.unused == 0


def test_a_rise_over_several_hours_is_shared_by_their_costs():
    r = [Reading(0, 0, W), Reading(3 * 3600, 6, W)]
    s = split(r, {(0, ("atlas",)): Decimal(1), (7200, ("notes",)): Decimal(2)}, None)
    assert s.limit == {("atlas",): 2.0, ("notes",): 4.0} and s.dollars == {}


def test_a_rise_with_no_turn_is_elsewhere_and_the_rest_at_reset_is_unused():
    r = [Reading(0, 10, W), Reading(3600, 14, W), Reading(7200, 20, W),
         Reading(W + 60, 2, 2 * W)]  # fmt: skip
    s = split(
        r, {(0, ("atlas",)): Decimal(1), (W, ("atlas",)): Decimal(1)}, Decimal(200)
    )
    assert s.limit == {("atlas",): 6.0}  # 4 before the reset, 2 after
    assert s.elsewhere == 6.0 and s.unused == 80.0


def test_slices_elsewhere_and_unused_add_up_to_the_weekly_fee():
    r = [Reading(0, 0, W), Reading(3600, 33, W), Reading(5 * 3600, 52.5, W),
         Reading(9 * 3600, 70, W), Reading(W, 0, 2 * W)]  # fmt: skip
    hourly = {(0, ("atlas",)): Decimal("2.5"), (0, ("notes",)): Decimal("0.7"),
              (3 * 3600, ("ledger",)): Decimal("1.1")}  # fmt: skip
    s = split(r, hourly, Decimal(200))
    total = sum(s.dollars.values()) + WEEK_FEE * Decimal(s.elsewhere + s.unused) / 100
    assert abs(total - WEEK_FEE) < Decimal("0.005")
    assert s.unused == 30.0 and s.elsewhere == 17.5  # hours 5 to 9 ran nothing
    assert round(sum(s.limit.values()), 9) == 52.5  # no point counted twice


def test_without_readings_the_fee_is_apportioned_by_cost():
    hourly = {(0, ("atlas",)): Decimal(3), (3600, ("notes",)): Decimal(1)}
    s = split([], hourly, Decimal(200), span=(0, W))
    assert s.kind == "apportioned" and s.limit == {}
    assert close(s.dollars[("atlas",)], WEEK_FEE * 3 / 4)
    assert close(s.dollars[("notes",)], WEEK_FEE / 4)
    assert close(s.apportioned, WEEK_FEE) and (s.elsewhere, s.unused) == (0, 0)


def test_hours_before_the_first_reading_are_apportioned_for_that_stretch():
    r = [Reading(W / 2, 0, W), Reading(W / 2 + 3600, 10, W)]
    hourly = {(0, ("atlas",)): Decimal(1), (W / 2, ("notes",)): Decimal(1)}
    s = split(r, hourly, Decimal(200), span=(0, W))
    assert s.kind == "metered" and s.limit == {("notes",): 10.0}
    assert close(s.dollars[("atlas",)], WEEK_FEE / 2)
    assert close(s.apportioned, WEEK_FEE / 2)
    assert close(s.dollars[("notes",)], WEEK_FEE / 10)


def test_a_subscription_without_a_fee_has_limit_only():
    s = split([], {(0, ("atlas",)): Decimal(1)}, None)
    assert (s.limit, s.dollars, s.apportioned) == ({}, {}, Decimal(0))
