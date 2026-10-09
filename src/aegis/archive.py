"""The archive, in pages, on one server or merged across linked ones.

A position is ``(last_activity, log_id)``: the archive is ordered newest first
by that pair, and a page starts strictly after the position the last one ended
on. A bare timestamp was the position before (#203), and a session sharing its
timestamp with the last row of a page was skipped; the log id breaks the tie.

A cursor is the positions of every server in a listing, as urlsafe base64 of
JSON, so the client hands back what it was given and never reads it. A server
whose archive is exhausted has the position ``None``.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Iterable

from .ops import OpError

Position = tuple[float, str]

SEARCHED = ("title", "handle", "cwd", "agent", "profile")


def key(meta: dict) -> Position:
    return (float(meta.get("last_activity") or 0), str(meta.get("log_id", "")))


def page(
    metas: Iterable[dict], query: str | None, limit: int, after: Position | None
) -> tuple[list[dict], int, Position | None]:
    """The next ``limit`` metas matching ``query`` after ``after``, the number
    matching in all, and where the next page starts (None: nothing is left)."""
    q = (query or "").lower()
    matching = sorted(
        (
            m
            for m in metas
            if not q or q in " ".join(str(m.get(k) or "") for k in SEARCHED).lower()
        ),
        key=key,
        reverse=True,
    )
    rest = matching if after is None else [m for m in matching if key(m) < after]
    items = rest[:limit]
    return items, len(matching), key(items[-1]) if len(rest) > limit else None


def merge(
    pages: dict[str, tuple[list[dict], Position | None]],
    limit: int,
    positions: dict[str, Position | None],
) -> tuple[list[dict], dict[str, Position | None]]:
    """Merge each server's next page, newest first, keep ``limit``, and say where
    each server now stands. ``pages`` maps a server to its items (each carrying
    ``server``) and the position its page ended on; a server missing from it, a
    linked one that is down, keeps its position."""
    pool = sorted(
        (m for items, _ in pages.values() for m in items), key=key, reverse=True
    )
    taken = pool[:limit]
    out = dict(positions)
    for name, (items, last) in pages.items():
        mine = [m for m in taken if m.get("server") == name]
        if len(mine) == len(items):
            out[name] = last  # the whole page went out
        elif mine:
            out[name] = key(mine[-1])
    return taken, out


def encode(positions: dict[str, Position | None]) -> str:
    raw = json.dumps(
        {k: list(v) if v is not None else None for k, v in positions.items()}
    )
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode(cursor: str | None) -> dict[str, Position | None]:
    if not cursor:
        return {}
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError
        out: dict[str, Position | None] = {}
        for k, v in data.items():
            if v is None:
                out[str(k)] = None
            elif (
                isinstance(v, list)
                and len(v) == 2
                and isinstance(v[0], int | float)
                and isinstance(v[1], str)
            ):
                out[str(k)] = (float(v[0]), v[1])
            else:
                raise ValueError
        return out
    except (ValueError, binascii.Error, UnicodeDecodeError) as e:
        raise OpError("bad_cursor", "not a cursor this server handed out") from e
