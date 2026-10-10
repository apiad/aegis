"""Day bounds for journal queries, in the server's local time: "what did we do
on Tuesday" means the person's Tuesday."""

from __future__ import annotations

import datetime as dt
import re
import time


def _start(day: dt.date) -> float:
    return time.mktime(day.timetuple())


def bound(text: str | None, *, end: bool, now: float | None = None) -> float | None:
    """``since`` (end=False) or ``until`` (end=True) as epoch seconds. A day is
    whole: since starts it, until ends it. Forms: 2026-10-09, today, yesterday,
    Nd (N days before now)."""
    if not text:
        return None
    now = time.time() if now is None else now
    t = text.strip().lower()
    if m := re.fullmatch(r"(\d+)d", t):
        return now - int(m[1]) * 86400
    today = dt.date.fromtimestamp(now)
    if t in ("today", "yesterday"):
        day = today - dt.timedelta(days=1 if t == "yesterday" else 0)
    else:
        try:
            day = dt.date.fromisoformat(t)
        except ValueError:
            raise ValueError(
                f"not a day: {text!r} (use 2026-10-09, today, yesterday or 7d)"
            ) from None
    return _start(day + dt.timedelta(days=1) if end else day)
