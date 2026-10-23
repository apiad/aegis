"""The usage log: one SQLite file, ``<state>/knowledge-usage.sqlite``.

Every hint writes one ``hinted`` row per item shown, with its rank; a read of
an item hinted earlier to the same session keeps that rank in ``detail``. The
pair is how the engine is judged on real use: the share of reads that came
from the hint, and at which rank. Stats and the Knowledge view are queries
over this table. Rows older than a year are dropped by a daily prune. Only
the server writes it, so it opens with WAL, as the journal does, and no
cross-process schema dance.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

EVENTS = (
    "hinted",
    "read",
    "invoked",
    "ran",
    "saved",
    "approved",
    "discarded",
    "deleted",
    "flagged",
)
KEEP_S = 365 * 86400
SCHEMA = """
CREATE TABLE IF NOT EXISTS usage(
  ts REAL, principal TEXT, session TEXT, item_kind TEXT, item_name TEXT,
  event TEXT, detail TEXT);
CREATE INDEX IF NOT EXISTS usage_item ON usage(principal, item_name);
CREATE INDEX IF NOT EXISTS usage_ts ON usage(ts)
"""


class Usage:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(
            path, timeout=30, isolation_level=None, check_same_thread=False
        )
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.executescript(SCHEMA)
        self._lock = threading.Lock()

    def add(
        self,
        principal: str,
        session: str,
        kind: str,
        name: str,
        event: str,
        detail: str | None = None,
        ts: float | None = None,
    ) -> None:
        if event not in EVENTS:
            raise ValueError(f"unknown usage event {event!r}")
        with self._lock:
            self._con.execute(
                "INSERT INTO usage VALUES (?,?,?,?,?,?,?)",
                (ts or time.time(), principal, session, kind, name, event, detail),
            )

    def summary(self, principal: str) -> dict[str, dict]:
        """Per item name: kind, a count per event, and ``last_used``, the
        last read, invocation or run (None when never)."""
        with self._lock:
            rows = self._con.execute(
                "SELECT item_name, item_kind, event, count(*), max(ts) FROM usage"
                " WHERE principal = ? GROUP BY item_name, item_kind, event",
                (principal,),
            ).fetchall()
        out: dict[str, dict] = {}
        for name, kind, event, n, last in rows:
            s = out.setdefault(
                name,
                {
                    "kind": kind,
                    "hinted": 0,
                    "read": 0,
                    "invoked": 0,
                    "ran": 0,
                    "last_used": None,
                },
            )
            s[event] = s.get(event, 0) + n
            if event in ("read", "invoked", "ran"):
                s["last_used"] = max(s["last_used"] or 0, last)
        return out

    def rows(self, principal: str, name: str | None = None) -> list[dict]:
        """Every row of ``principal`` (of one item, given ``name``), oldest first."""
        sql = "SELECT ts, session, item_kind, item_name, event, detail FROM usage WHERE principal = ?"
        args: list = [principal]
        if name is not None:
            sql += " AND item_name = ?"
            args.append(name)
        with self._lock:
            got = self._con.execute(sql + " ORDER BY ts, rowid", args).fetchall()
        keys = ("ts", "session", "kind", "name", "event", "detail")
        return [dict(zip(keys, r)) for r in got]

    def prune(self, now: float | None = None) -> int:
        """Drop rows older than a year; how many went."""
        with self._lock:
            cur = self._con.execute(
                "DELETE FROM usage WHERE ts < ?", ((now or time.time()) - KEEP_S,)
            )
        return cur.rowcount
