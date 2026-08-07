"""Title precedence and sanitizing — pure, no I/O.

A title is a label; the handle is identity. These two functions are the
whole concurrency story for titles: a write is applied only when its
source outranks (or equals) the current one, so a slow auto-generation
landing after the operator typed ``/title`` is discarded on arrival
rather than winning the race. No request ids, no in-flight bookkeeping.
"""
from __future__ import annotations

TITLE_RANK: dict[str, int] = {"": 0, "auto": 1, "agent": 2, "human": 3}

# The tab cell already carries a state dot, an index, the handle, the slug
# and a muted suffix, and the bar scrolls sideways — so this is deliberately
# far below t3code's 50.
DEFAULT_CAP = 32

_STRIP_CHARS = "\"'`“”‘’ \t"
_STRIP_TRAILING = ".,;:!-–—"


def outranks(new_source: str, current_source: str) -> bool:
    """May a write from ``new_source`` overwrite a title set by
    ``current_source``?

    Equal ranks may — a human retyping their own title is not a conflict.
    Unknown sources rank lowest, so a typo can never win.
    """
    return TITLE_RANK.get(new_source, 0) >= TITLE_RANK.get(current_source, 0)


def sanitize_title(text: str, *, cap: int = DEFAULT_CAP) -> str:
    """First line only, unwrapped, collapsed, capped.

    Returns ``""`` when nothing usable survives — callers treat that as
    "leave it unset" rather than storing an empty label. A model that
    ignores every instruction still yields a usable tab label.
    """
    if not text or not text.strip():
        return ""
    line = text.strip().splitlines()[0]
    line = line.strip(_STRIP_CHARS)
    line = " ".join(line.split())
    line = line.rstrip(_STRIP_TRAILING)
    if not line:
        return ""
    if len(line) <= cap:
        return line
    head = line[:cap - 1]
    cut = head.rsplit(" ", 1)[0] if " " in head else head
    return f"{cut}…"
