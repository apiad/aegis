"""The clock of a schedule: every, cron and at, parsed and searched."""

import time
from datetime import datetime

import pytest

from aegis.clock import ClockError, clock_from, parse_cron, parse_every


@pytest.fixture(autouse=True)
def new_york(monkeypatch):
    """A zone with DST, so the tests mean the same on any machine."""
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def ts(s: str) -> int:
    return int(datetime.fromisoformat(s).timestamp())


def local(t: int) -> str:
    return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M %a")


def test_every_takes_minutes_hours_and_days_and_nothing_below_a_minute():
    assert [parse_every(x) for x in ("5m", "2h", "1d")] == [300, 7200, 86400]
    for bad in ("30s", "0m", "5", "m5", "1.5h", ""):
        with pytest.raises(ClockError):
            parse_every(bad)


@pytest.mark.parametrize(
    "line,after,want",
    [
        ("0 6 * * 1-5", "2026-10-16 07:00", "2026-10-19 06:00 Mon"),
        ("*/15 * * * *", "2026-10-14 09:07", "2026-10-14 09:15 Wed"),
        ("0 0 1,15 * *", "2026-10-15 00:00", "2026-11-01 00:00 Sun"),
        ("30 23 31 12 *", "2026-12-31 23:30", "2027-12-31 23:30 Fri"),
        ("0 9 * jan-mar mon", "2026-10-14 00:00", "2027-01-04 09:00 Mon"),
        # both day fields restricted: either matches (Vixie cron)
        ("0 12 13 * fri", "2026-10-14 00:00", "2026-10-16 12:00 Fri"),
        ("@daily", "2026-10-14 09:00", "2026-10-15 00:00 Thu"),
        ("@hourly", "2026-10-14 09:00", "2026-10-14 10:00 Wed"),
        ("0 6 * * 7", "2026-10-14 00:00", "2026-10-18 06:00 Sun"),
        ("0 6 * * sun", "2026-10-14 00:00", "2026-10-18 06:00 Sun"),
        ("0-10/5 8 * * *", "2026-10-14 08:05", "2026-10-14 08:10 Wed"),
    ],
)
def test_cron_finds_the_next_tick(line, after, want):
    assert local(parse_cron(line).next_after(ts(after))) == want


def test_cron_keeps_wall_time_across_a_dst_change():
    # 2026-03-08 02:00 is when New York springs forward: that day has 23 hours.
    c = parse_cron("0 6 * * *")
    a = c.next_after(ts("2026-03-07 05:00"))
    b = c.next_after(a)
    assert local(b) == "2026-03-08 06:00 Sun" and b - a == 23 * 3600


@pytest.mark.parametrize(
    "line",
    ["61 * * * *", "* * *", "a * * * *", "*/0 * * * *", "5-2 * * * *", "0 0 31 2 *"],
)
def test_a_bad_or_dead_cron_line_is_refused(line):
    with pytest.raises(ClockError):
        parse_cron(line)


def test_every_counts_from_its_anchor_and_coalesces_missed_ticks():
    anchor = ts("2026-10-14 09:00")
    c = clock_from({"every": "5m"})
    assert c.due(anchor, anchor + 299, anchor) == (None, 0)
    assert c.due(anchor, anchor + 300, anchor) == (anchor + 300, 1)
    assert c.due(anchor, anchor + 3 * 300 + 10, anchor) == (anchor + 900, 3)
    assert c.due(anchor + 900, anchor + 1000, anchor) == (None, 0)
    assert c.next_after(anchor + 10, anchor) == anchor + 300


def test_at_ticks_once():
    c = clock_from({"at": "2026-10-15 09:00"})
    t = ts("2026-10-15 09:00")
    assert c.due(t - 600, t - 1, t - 600) == (None, 0)
    assert c.due(t - 600, t + 3600, t - 600) == (t, 1)
    assert c.due(t, t + 7200, t - 600) == (None, 0)
    assert c.next_after(t + 1, 0) is None


def test_cron_due_counts_every_tick_missed():
    c = clock_from({"cron": "0 * * * *"})
    last = ts("2026-10-14 09:00")
    assert c.due(last, ts("2026-10-14 12:30"), last) == (ts("2026-10-14 12:00"), 3)


@pytest.mark.parametrize(
    "raw", [{}, {"every": "5m", "at": "x"}, {"hourly": "x"}, {"at": "tomorrow"}]
)
def test_a_clock_is_exactly_one_known_form(raw):
    with pytest.raises(ClockError):
        clock_from(raw)
