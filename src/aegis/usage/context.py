"""Context bands and the handoff replay (spec 09).

A call's context is its input plus cache read plus cache writes. The replay
asks, for each threshold, what the session would have cost had it handed off
whenever its context passed the threshold: the successor starts at ``start``
tokens, every later call carries the dropped tokens less, priced at cache
read, and each handoff pays one cache write of the start. Rereading, what a
real successor spends reading its way back in, is measured separately
(``reread_cost``) and subtracted at query time.
"""

from __future__ import annotations

import sqlite3
from decimal import Decimal
from statistics import median

from .prices import Prices

BANDS = (0, 50_000, 100_000, 200_000, 300_000, 400_000, 600_000)
THRESHOLDS = (100_000, 150_000, 200_000, 300_000, 400_000)
START = 30_000  # a successor's start context when no successor has run here
_M = Decimal(1_000_000)


def band(ctx: int) -> int:
    """The index of the band ``ctx`` falls in."""
    return max(k for k, lo in enumerate(BANDS) if ctx >= lo)


class Replay:
    """One session's calls replayed with a handoff at each threshold."""

    def __init__(self, start: int = START) -> None:
        self.start = start
        self.offset = {at: 0 for at in THRESHOLDS}  # tokens a handoff dropped

    def call(self, ctx: int, prices: Prices) -> dict[int, tuple[int, Decimal, Decimal]]:
        """This call's (handoffs, saved, rewrite) per threshold passed so far."""
        out = {}
        for at, off in self.offset.items():
            handoffs, rewrite = 0, Decimal(0)
            if ctx - off > at:
                off = self.offset[at] = max(ctx - self.start, 0)
                handoffs, rewrite = 1, self.start * prices.cache_write_5m / _M
            if off:
                out[at] = (handoffs, off * prices.cache_read / _M, rewrite)
        return out


def successor_start(con: sqlite3.Connection) -> int:
    """The median start context of successors' first turns here, or START."""
    rows = [
        r
        for (r,) in con.execute(
            "SELECT ctx_start FROM usage_turns WHERE continues IS NOT NULL"
            " AND ctx_start > 0"
        )
    ]
    return int(median(rows)) if rows else START


def reread_cost(con: sqlite3.Connection) -> Decimal:
    """The median, over successor sessions, of the reading activity's cost in
    their first five turns."""
    rows = con.execute(
        "WITH s AS (SELECT DISTINCT log_id FROM usage_turns WHERE continues IS NOT NULL),"
        " f AS (SELECT t.log_id, t.rec, ROW_NUMBER() OVER"
        " (PARTITION BY t.log_id ORDER BY t.rec) AS k FROM usage_turns t"
        " JOIN s ON s.log_id = t.log_id)"
        " SELECT f.log_id, COALESCE(SUM(p.cost), 0) FROM f LEFT JOIN usage_parts p"
        " ON p.log_id = f.log_id AND p.rec = f.rec AND p.activity = 'reading'"
        " WHERE f.k <= 5 GROUP BY f.log_id"
    ).fetchall()
    if not rows:
        return Decimal(0)
    return Decimal(str(round(median(c for _, c in rows), 9)))
