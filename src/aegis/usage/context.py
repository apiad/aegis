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


def threshold(text: str) -> int:
    """``200k`` or ``200000`` as one of THRESHOLDS, else a ValueError that
    names them."""
    t = text.strip().lower()
    n = int(float(t[:-1]) * 1000) if t.endswith("k") else int(t) if t.isdigit() else 0
    if n not in THRESHOLDS:
        raise ValueError(
            "whatif takes "
            + ", ".join(f"{x // 1000}k" for x in THRESHOLDS)
            + f", not {text!r}"
        )
    return n


def band_label(k: int) -> str:
    """Band ``k`` as the page and the CLI name it: 0–50k, 600k+."""
    lo = f"{BANDS[k] // 1000}k" if BANDS[k] else "0"
    return f"{lo}–{BANDS[k + 1] // 1000}k" if k + 1 < len(BANDS) else f"{lo}+"


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


def summary(
    con: sqlite3.Connection, since: float, until: float, people: set[str] | None = None
) -> dict:
    """The context panel's facts for turns ending in the window: calls, cost
    and cache reads per band; each session's peak and whether a successor
    continued it; the replay's sums per threshold and the rereading cost."""
    where = "t.ts >= ? AND t.ts < ?"
    args: list = [since, until]
    if people is not None:
        where += f" AND t.person IN ({','.join('?' * len(people))})"
        args += sorted(people)
    bands = [
        {"band": b, "calls": n, "cost": round(c or 0, 9), "tok_cr": cr or 0}
        for b, n, c, cr in con.execute(
            "SELECT c.band, SUM(c.calls), SUM(c.cost), SUM(c.tok_cr) FROM usage_ctx c"
            " JOIN usage_turns t ON t.log_id = c.log_id AND t.rec = c.rec"
            f" WHERE {where} GROUP BY c.band ORDER BY c.band",
            args,
        )
    ]
    handed = {
        c
        for (c,) in con.execute(
            "SELECT DISTINCT continues FROM usage_turns WHERE continues IS NOT NULL"
        )
    }
    peaks = [
        [peak, log_id in handed]
        for log_id, peak in con.execute(
            f"SELECT t.log_id, MAX(t.ctx_peak) FROM usage_turns t WHERE {where}"
            " GROUP BY t.log_id",
            args,
        )
        if peak
    ]
    got = {
        at: (h, s, r)
        for at, h, s, r in con.execute(
            "SELECT w.at, SUM(w.handoffs), SUM(w.saved), SUM(w.rewrite)"
            " FROM usage_whatif w JOIN usage_turns t ON t.log_id = w.log_id"
            f" AND t.rec = w.rec WHERE {where} GROUP BY w.at",
            args,
        )
    }
    reread = float(reread_cost(con))
    replay = [
        {"at": at, "handoffs": got.get(at, (0, 0, 0))[0],
         "saved": round(got.get(at, (0, 0, 0))[1], 9),
         "rewrite": round(got.get(at, (0, 0, 0))[2], 9), "reread": reread}
        for at in THRESHOLDS
    ]  # fmt: skip
    return {"bands": bands, "peaks": peaks, "replay": replay}
