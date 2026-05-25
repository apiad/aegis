"""Cron expression evaluation with timezone awareness.

Wraps ``croniter`` so the scheduler can compute the next fire time
of a 5-field cron expression in any IANA timezone, returning a
timezone-aware ``datetime`` for direct comparison with
``Clock.now()``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from croniter import croniter


def next_fire(cron_expr: str, tz: str, after: datetime) -> datetime:
    """Return the next fire time strictly after ``after``.

    ``cron_expr`` is a standard 5-field cron string. ``tz`` is an
    IANA timezone name (e.g. ``"America/Havana"``); the cron is
    evaluated in *that* timezone, but the returned datetime is in
    UTC so the scheduler can compare against ``Clock.now()`` without
    worrying about the input timezone.
    """
    if after.tzinfo is None:
        after = after.replace(tzinfo=timezone.utc)
    local = after.astimezone(ZoneInfo(tz))
    it = croniter(cron_expr, local)
    nxt_local = it.get_next(datetime)
    return nxt_local.astimezone(timezone.utc)
