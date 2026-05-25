"""Tests for cron → next_fire computation."""
from __future__ import annotations

from datetime import datetime, timezone

from aegis.scheduler.cron import next_fire


def test_next_fire_simple() -> None:
    now = datetime(2026, 5, 25, 1, 30, tzinfo=timezone.utc)
    nxt = next_fire("0 2 * * *", "UTC", now)
    assert nxt == datetime(2026, 5, 25, 2, 0, tzinfo=timezone.utc)


def test_next_fire_wraps_to_next_day() -> None:
    now = datetime(2026, 5, 25, 3, 30, tzinfo=timezone.utc)
    nxt = next_fire("0 2 * * *", "UTC", now)
    assert nxt == datetime(2026, 5, 26, 2, 0, tzinfo=timezone.utc)


def test_next_fire_timezone() -> None:
    """02:00 America/Havana is 06:00 UTC during EDT-equivalent."""
    now = datetime(2026, 5, 25, 1, 30, tzinfo=timezone.utc)
    nxt = next_fire("0 2 * * *", "America/Havana", now)
    assert nxt.tzinfo is not None
    assert nxt.utcoffset().total_seconds() == 0
    havana_local = nxt.astimezone(__import__("zoneinfo").ZoneInfo(
        "America/Havana"))
    assert havana_local.hour == 2
    assert havana_local.minute == 0


def test_next_fire_naive_now_treated_as_utc() -> None:
    now = datetime(2026, 5, 25, 1, 30)
    nxt = next_fire("0 2 * * *", "UTC", now)
    assert nxt == datetime(2026, 5, 25, 2, 0, tzinfo=timezone.utc)


def test_every_minute() -> None:
    now = datetime(2026, 5, 25, 1, 30, 30, tzinfo=timezone.utc)
    nxt = next_fire("* * * * *", "UTC", now)
    assert nxt == datetime(2026, 5, 25, 1, 31, tzinfo=timezone.utc)
