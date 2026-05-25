"""Clock abstraction so the scheduler can be tested deterministically.

``SystemClock`` returns wall-clock UTC; ``FakeClock`` carries a
mutable "now" that tests advance with ``clock.advance(seconds=…)``
or ``clock.advance(minutes=…)``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FakeClock:
    """Test clock — starts at ``start``, advance with ``advance()``."""

    def __init__(self, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, **kwargs) -> None:
        """Move the clock forward by the supplied timedelta kwargs."""
        self._now += timedelta(**kwargs)
