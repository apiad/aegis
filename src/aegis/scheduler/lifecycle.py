"""Lifecycle exhaustion predicate for scheduler entries.

A schedule's ``lifecycle`` controls when it stops firing:

- ``"forever"`` — never exhausted (default; cron triggers).
- ``"once"`` — exhausted after a single fire (canonical for ``fire_at``).
- ``{"fires": N}`` — exhausted after N completed fires.
- ``{"until": "<iso>"}`` — exhausted once wall-clock passes the ISO instant.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any


def is_exhausted(lifecycle: Any, *, fire_count: int, now: datetime) -> bool:
    if lifecycle == "forever":
        return False
    if lifecycle == "once":
        return fire_count >= 1
    if isinstance(lifecycle, dict):
        if "fires" in lifecycle:
            return fire_count >= int(lifecycle["fires"])
        if "until" in lifecycle:
            return now > datetime.fromisoformat(lifecycle["until"])
    raise ValueError(f"invalid lifecycle: {lifecycle!r}")
