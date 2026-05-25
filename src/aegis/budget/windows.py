"""Parse window strings like '30m', '1h', '24h', '7d', '1w' to timedelta."""
from __future__ import annotations

from datetime import timedelta

_SUFFIXES = {
    "m": "minutes",
    "h": "hours",
    "d": "days",
    "w": "weeks",
}


def parse_window(s: str) -> timedelta:
    """Convert a window string to a positive timedelta.

    Accepted: ``Nm`` (minutes), ``Nh`` (hours), ``Nd`` (days), ``Nw``
    (weeks). N must be a positive integer.
    """
    if not s:
        raise ValueError("window string is empty")
    suffix = s[-1].lower()
    if suffix not in _SUFFIXES:
        if suffix.isdigit():
            raise ValueError(f"window {s!r} must end with one of m/h/d/w")
        raise ValueError(f"unknown window suffix {suffix!r} in {s!r}")
    try:
        n = int(s[:-1])
    except ValueError:
        raise ValueError(f"window {s!r} prefix must be an integer")
    if n <= 0:
        raise ValueError(f"window {s!r} must be positive")
    return timedelta(**{_SUFFIXES[suffix]: n})
