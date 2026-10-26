"""A subscription's fee and weekly limit, split over what ran on it (spec 08).

A rise of the weekly percent between two readings goes to the groups that ran
in the hours that interval covers, by their cost at API prices; a rise with
no cost behind it is elsewhere (the provider's own apps, a machine aegis does
not run on). At a reset, what the window had not used is unused. A week of
the fee is its monthly fee times 12 / 52, and a slice's dollars are that
weekly fee times the points it used over 100, so the slices, elsewhere and
unused add up to the fee. Hours before the first reading (history, imports,
a harness with no quota provider) are apportioned: that stretch's fee split
by cost. Costs come by hour because a turn's parts are grouped by hour on
every server, which is what a merge across servers can share.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

WEEK = 7 * 86400
HOUR = 3600
# The window a fee is split by, per quota provider: the longest one every
# model counts against.
WEEKLY = {"claude": "weekly_all", "opencode-go": "weekly"}


@dataclass(frozen=True)
class Reading:
    ts: float
    percent: float
    resets_at: float | None


@dataclass
class SubSplit:
    limit: dict[tuple, float]  # points of the weekly limit, per group
    dollars: dict[tuple, Decimal]  # subscription cost, per group
    elsewhere: float  # points used with no turn behind them
    unused: float  # points left at a reset
    kind: str  # metered or apportioned
    apportioned: Decimal = Decimal(0)  # dollars split by cost, without readings


def weekly_fee(fee_month: Decimal) -> Decimal:
    return fee_month * 12 / 52


def _apportion(hours: dict, fee: Decimal, into: dict) -> None:
    by: dict[tuple, Decimal] = defaultdict(Decimal)
    for (_, key), cost in hours.items():
        by[key] += cost
    total = sum(by.values(), Decimal(0))
    if total:
        for key, cost in by.items():
            into[key] = into.get(key, Decimal(0)) + fee * cost / total


def split(
    readings: list[Reading],
    hourly: dict[tuple[int, tuple], Decimal],
    fee_month: Decimal | None,
    span: tuple[float, float] | None = None,
) -> SubSplit:
    """Share the readings' rises over ``hourly`` (hour start, group) -> cost
    at API prices. ``span`` is the period asked, whose fee is apportioned
    where there are no readings; by default the hours' own span."""
    week = weekly_fee(fee_month) if fee_month is not None else None
    readings = sorted(readings, key=lambda r: r.ts)
    first = readings[0].ts if readings else float("inf")
    before = {k: c for k, c in hourly.items() if k[0] + HOUR <= first}
    dollars: dict[tuple, Decimal] = {}
    apportioned = Decimal(0)
    if before and week is not None:
        since = span[0] if span else min(h for h, _ in before)
        until = (
            min(first, span[1])
            if span
            else min(first, max(h for h, _ in before) + HOUR)
        )
        apportioned = week * Decimal(str(max(until - since, 0))) / WEEK
        _apportion(before, apportioned, dollars)
    if not readings:
        return SubSplit({}, dollars, 0.0, 0.0, "apportioned", apportioned)
    by_hour: dict[int, list[tuple[tuple, Decimal]]] = defaultdict(list)
    for (h, key), cost in hourly.items():
        by_hour[int(h)].append((key, cost))
    limit: dict[tuple, Decimal] = defaultdict(Decimal)
    elsewhere = unused = Decimal(0)
    for a, b in zip(readings, readings[1:]):
        pa, pb = Decimal(str(a.percent)), Decimal(str(b.percent))
        reset = pb < pa or (b.resets_at or 0) - (a.resets_at or 0) > HOUR
        if reset:
            unused += max(Decimal(100) - pa, Decimal(0))
        rise = pb if reset else pb - pa
        if rise <= 0:
            continue
        ran: dict[tuple, Decimal] = defaultdict(Decimal)
        h = int(a.ts // HOUR * HOUR)
        while h < b.ts:
            for key, cost in by_hour.get(h, ()):
                ran[key] += cost
            h += HOUR
        total = sum(ran.values(), Decimal(0))
        if not total:
            elsewhere += rise
            continue
        for key, cost in ran.items():
            limit[key] += rise * cost / total
    if week is not None:
        for key, points in limit.items():
            dollars[key] = dollars.get(key, Decimal(0)) + week * points / 100
    return SubSplit(
        {k: float(v) for k, v in limit.items()},
        dollars,
        float(elsewhere),
        float(unused),
        "metered",
        apportioned,
    )
