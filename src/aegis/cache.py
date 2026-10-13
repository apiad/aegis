"""What the prompt cache does for a session, from the usage its calls report.

After the cache expires, the next call pays for the whole context again as a
cache write instead of a read. Measured over 7,033 Claude Code transcripts in
the 30 days to 2026-09-29: 99% of calls under 5 minutes after the previous one
hit the cache, 90% between 5 minutes and an hour, 1% past an hour. Re-measured
for #354 over the 7 days to 2026-10-12: 266 of 266 resumes of a context over
100k after 5 to 60 minutes hit, and 17 of 17 after more than an hour missed.
That puts Claude Code's TTL at one hour. A write at that TTL bills at twice
the input rate and a read at a tenth, so a cold resume costs about 20 times a
warm one.

Only measured harnesses are listed; for any other the warning and the alarm
stay silent rather than guess.
"""

from __future__ import annotations

from .claude.stream import Usage

CACHE_TTL_S: dict[str, int] = {"claude-code": 3600}
# How many of a session's latest calls the hit rate covers.
RECENT = 10
# A warm session's hit rate below this, in percent, means something broke the
# cache: a model switch, an MCP server added mid-session, a changed prefix.
ALARM_BELOW = 80


def push(recent: list[list], usage: Usage, at: float) -> list[list]:
    """The window with this call added: ``[cache_read, total_input, at]``."""
    total = usage.input + usage.cache_creation + usage.cache_read
    if total <= 0:
        return recent
    return [*recent, [usage.cache_read, total, at]][-RECENT:]


def _rate(entries: list[list]) -> int | None:
    total = sum(e[1] for e in entries)
    return round(100 * sum(e[0] for e in entries) / total) if total else None


def hit_rate(recent: list[list]) -> int | None:
    """Cache reads over all input in the window, in percent; None when empty."""
    return _rate(recent)


def alarm(recent: list[list], harness: str) -> bool:
    """Below ``ALARM_BELOW`` over the warm calls alone. A call that came a TTL
    or more after the one before it missed by design, and the cold-cache
    notice already said so; the first call of a window has no gap to judge."""
    ttl = CACHE_TTL_S.get(harness)
    if ttl is None:
        return False
    warm = [b for a, b in zip(recent, recent[1:]) if b[2] - a[2] < ttl]
    rate = _rate(warm)
    return rate is not None and rate < ALARM_BELOW


def cold_policy(harness: str, context: dict) -> dict | None:
    """The thresholds the client checks for the cold-cache notice, or None."""
    if harness not in CACHE_TTL_S:
        return None
    return {"after": context["cold_after"], "min": context["cold_min"]}
