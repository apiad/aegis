"""A schedule's clock: ``every``, ``cron`` and ``at``, in the server's local
zone (docs/superpowers/specs/2026-10-14-schedules-design.html, section 04).

A clock makes ticks and never fires. Ticks are whole seconds since the epoch,
so a tick written to the run log and read back is the same number, and an
``every`` counts from an anchor that is whole seconds too: the schedule's
creation or its last edit.

Cron is five fields: minute, hour, day of month, month, day of week. Each is
``*``, a number, a range ``a-b``, a list ``a,b`` or a step ``*/n`` or
``a-b/n``; months and days also take names (``jan``, ``mon``), and day 7 is
Sunday like 0. ``@hourly``, ``@daily`` and ``@weekly`` stand for their lines.
When both day fields are restricted, a day matches if either does, as in
Vixie cron. The next tick is searched in local wall time and converted at the
end, so ``0 6 * * *`` is six in the morning on both sides of a DST change. A
line with no tick in the next 366 days is refused as dead.
"""

from __future__ import annotations

import functools
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

MIN_EVERY_S = 60
SEARCH_DAYS = 366
# A clock that missed more ticks than this while aegis was down says this many.
COUNT_CAP = 10_000
UNITS = {"m": 60, "h": 3600, "d": 86400}
MACROS = {"@hourly": "0 * * * *", "@daily": "0 0 * * *", "@weekly": "0 0 * * 0"}
MONTHS: dict[str, int] = {
    n: i + 1
    for i, n in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())
}
DAYS: dict[str, int] = {
    n: i for i, n in enumerate("sun mon tue wed thu fri sat".split())
}


class ClockError(ValueError):
    """A clock value that does not parse, saying what is wrong."""


def parse_every(text: str) -> int:
    m = re.fullmatch(r"\s*(\d+)([mhd])\s*", text)
    if not m:
        raise ClockError(
            f"every {text!r}: write a number and m, h or d, as in 5m, 2h or 1d"
        )
    seconds = int(m[1]) * UNITS[m[2]]
    if seconds < MIN_EVERY_S:
        raise ClockError("the smallest interval is 1m")
    return seconds


def _value(x: str, names: dict[str, int]) -> int:
    if x in names:
        return names[x]
    if x.isdigit():
        return int(x)
    raise ClockError(f"{x!r} is not a number or a name")


def _field(text: str, lo: int, hi: int, names: dict[str, int]) -> frozenset[int]:
    out: set[int] = set()
    for part in text.lower().split(","):
        rng, slash, step = part.partition("/")
        if slash and not step.isdigit():
            raise ClockError(f"{part!r}: a step is a number")
        n = int(step) if slash else 1
        if n < 1:
            raise ClockError(f"{part!r}: a step is at least 1")
        if rng == "*":
            a, b = lo, hi
        else:
            x, dash, y = rng.partition("-")
            a = _value(x, names)
            b = _value(y, names) if dash else (hi if slash else a)
        if not (lo <= a <= b <= hi):
            raise ClockError(f"{part!r} is outside {lo}-{hi}")
        out.update(range(a, b + 1, n))
    return frozenset(out)


@dataclass(frozen=True)
class Cron:
    minute: frozenset[int]
    hour: frozenset[int]
    dom: frozenset[int]
    month: frozenset[int]
    dow: frozenset[int]
    any_dom: bool
    any_dow: bool

    def _day(self, d: datetime) -> bool:
        dom, dow = d.day in self.dom, d.isoweekday() % 7 in self.dow
        if self.any_dom or self.any_dow:
            return dom and dow
        return dom or dow

    def next_after(self, t: float) -> int | None:
        d = datetime.fromtimestamp(t).replace(second=0, microsecond=0)
        d += timedelta(minutes=1)
        end = d + timedelta(days=SEARCH_DAYS)
        while d <= end:
            if d.month not in self.month:
                d = (d.replace(day=1, hour=0, minute=0) + timedelta(days=32)).replace(
                    day=1
                )
            elif not self._day(d):
                d = d.replace(hour=0, minute=0) + timedelta(days=1)
            elif d.hour not in self.hour:
                d = d.replace(minute=0) + timedelta(hours=1)
            elif d.minute not in self.minute:
                d += timedelta(minutes=1)
            else:
                tick = int(d.timestamp())
                if tick > t:
                    return tick
                d += timedelta(minutes=1)
        return None


def parse_cron(text: str) -> Cron:
    parts = MACROS.get(text.strip(), text).split()
    if len(parts) != 5:
        raise ClockError(f"cron {text!r}: a cron line has five fields")
    try:
        c = Cron(
            minute=_field(parts[0], 0, 59, {}),
            hour=_field(parts[1], 0, 23, {}),
            dom=_field(parts[2], 1, 31, {}),
            month=_field(parts[3], 1, 12, MONTHS),
            dow=frozenset(d % 7 for d in _field(parts[4], 0, 7, DAYS)),
            any_dom=parts[2] == "*",
            any_dow=parts[4] == "*",
        )
    except ClockError as e:
        raise ClockError(f"cron {text!r}: {e}") from None
    if c.next_after(time.time()) is None:
        raise ClockError(f"cron {text!r} has no tick in the next {SEARCH_DAYS} days")
    return c


def parse_at(text: str) -> int:
    try:
        return int(datetime.fromisoformat(text.strip().replace(" ", "T")).timestamp())
    except ValueError:
        raise ClockError(f"at {text!r}: write YYYY-MM-DD HH:MM, server time") from None


@dataclass(frozen=True)
class Clock:
    kind: str  # "every" | "cron" | "at"
    value: str
    every_s: int = 0
    cron: Cron | None = None
    at: int = 0

    def next_after(self, t: float, anchor: int) -> int | None:
        """The first tick after ``t``."""
        if self.kind == "every":
            k = max(0, (int(t) - anchor) // self.every_s + 1)
            return anchor + k * self.every_s
        if self.kind == "at":
            return self.at if self.at > t else None
        assert self.cron is not None
        return self.cron.next_after(t)

    def due(self, last: int, now: float, anchor: int) -> tuple[int | None, int]:
        """The latest tick in (last, now] and how many ticks fell there."""
        if self.kind == "every":
            n = (int(now) - anchor) // self.every_s - (last - anchor) // self.every_s
            if n <= 0:
                return None, 0
            return anchor + ((int(now) - anchor) // self.every_s) * self.every_s, min(
                n, COUNT_CAP
            )
        if self.kind == "at":
            return (self.at, 1) if last < self.at <= now else (None, 0)
        assert self.cron is not None
        latest, n, t = None, 0, float(last)
        while n < COUNT_CAP:
            nxt = self.cron.next_after(t)
            if nxt is None or nxt > now:
                break
            latest, n, t = nxt, n + 1, float(nxt)
        return latest, n

    def upcoming(self, after: float, anchor: int, k: int = 3) -> list[int]:
        out: list[int] = []
        t = after
        while len(out) < k and (nxt := self.next_after(t, anchor)) is not None:
            out.append(nxt)
            t = nxt
        return out


def clock_from(d: dict) -> Clock:
    """A clock from its stored form: one key, ``every``, ``cron`` or ``at``."""
    if not isinstance(d, dict) or len(d) != 1:
        raise ClockError("a clock is one of every, cron or at")
    ((kind, value),) = d.items()
    return _parse(str(kind), str(value))


@functools.lru_cache(maxsize=256)
def _parse(kind: str, value: str) -> Clock:
    # Cached: the clock task asks for every schedule's clock on each wake, and
    # a sparse cron line's dead-line search walks up to a year.
    if kind == "every":
        return Clock("every", value, every_s=parse_every(value))
    if kind == "cron":
        return Clock("cron", value, cron=parse_cron(value))
    if kind == "at":
        return Clock("at", value, at=parse_at(value))
    raise ClockError(f"{kind!r} is not a clock: every, cron or at")
