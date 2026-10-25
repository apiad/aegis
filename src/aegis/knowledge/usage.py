"""The usage log: one SQLite file, ``<state>/knowledge-usage.sqlite``.

Every hint writes one ``hinted`` row per item shown, with its rank; a read of
an item hinted earlier to the same session keeps that rank in ``detail``. The
pair is how the engine is judged on real use: the share of reads that came
from the hint, and at which rank. Stats and the Knowledge view are queries
over this table. Rows older than a year are dropped by a daily prune, which
then runs VACUUM. The ``flags`` table holds each item's raised flags, so a
restart's first check logs ``flagged`` only for a flag that is new.

Writes never run on the event loop: as the journal does, the loop only
enqueues and one writer thread owns the writing connection (WAL,
``synchronous=NORMAL``, a short busy timeout). A row the thread cannot write
(the file locked past the timeout, the disk full) is logged and dropped, so a
send never waits on or fails for the log. Reads use their own connection and
first wait for what is queued; callers on the loop run them in a thread.
"""

from __future__ import annotations

import logging
import queue
import sqlite3
import threading
import time
from pathlib import Path

log = logging.getLogger("aegis.knowledge")

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
BUSY_MS = 1000
SCHEMA = """
CREATE TABLE IF NOT EXISTS usage(
  ts REAL, principal TEXT, session TEXT, item_kind TEXT, item_name TEXT,
  event TEXT, detail TEXT);
CREATE INDEX IF NOT EXISTS usage_item ON usage(principal, item_name);
CREATE INDEX IF NOT EXISTS usage_ts ON usage(ts);
CREATE TABLE IF NOT EXISTS flags(
  principal TEXT, item_name TEXT, flag TEXT,
  PRIMARY KEY(principal, item_name, flag))
"""


def _connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(
        path, timeout=BUSY_MS / 1000, isolation_level=None, check_same_thread=False
    )
    con.execute(f"PRAGMA busy_timeout={BUSY_MS}")
    # The switch to WAL answers "locked" at once, past the busy timeout, so a
    # first connect that meets a held lock waits for it here (#731).
    end = time.monotonic() + BUSY_MS / 1000
    while con.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
        try:
            con.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower() or time.monotonic() > end:
                raise
            time.sleep(0.01)
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(SCHEMA)
    return con


class Usage:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._q: queue.SimpleQueue = queue.SimpleQueue()
        self._failed: set[str] = set()  # causes already logged
        self._read: sqlite3.Connection | None = None
        self._read_lock = threading.Lock()
        self._thread = threading.Thread(
            target=self._write, name="knowledge-usage", daemon=True
        )
        self._thread.start()

    # -- the writer thread ---------------------------------------------------
    def _write(self) -> None:
        con: sqlite3.Connection | None = None
        while True:
            item = self._q.get()
            if item is None:
                if con is not None:
                    con.close()
                return
            if item[0] == "sync":
                item[1].set()
                continue
            try:
                if con is None:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    con = _connect(self.path)
                _, sql, args = item
                cur = con.execute(sql, args)
                if sql.startswith("DELETE FROM usage") and cur.rowcount > 0:
                    con.execute("VACUUM")
            except (sqlite3.Error, OSError) as e:
                cause = f"{type(e).__name__}: {e}"
                if cause not in self._failed:
                    self._failed.add(cause)
                    log.warning("knowledge usage: dropped a write: %s", cause)

    def flush(self) -> None:
        """Wait until everything queued is written or dropped."""
        if not self._thread.is_alive():
            return
        done = threading.Event()
        self._q.put(("sync", done))
        while not done.wait(0.1):
            if not self._thread.is_alive():
                return

    def stop(self) -> None:
        """Write what is queued, then end the writer thread."""
        if self._thread.is_alive():
            self._q.put(None)
            self._thread.join(timeout=10)
        with self._read_lock:
            if self._read is not None:
                self._read.close()
                self._read = None

    def _query(self, sql: str, args) -> list:
        self.flush()
        with self._read_lock:
            if self._read is None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._read = _connect(self.path)
            return self._read.execute(sql, args).fetchall()

    # -- the API ----------------------------------------------------------------
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
        """Queue one row; returns at once."""
        if event not in EVENTS:
            raise ValueError(f"unknown usage event {event!r}")
        self._q.put(
            (
                "sql",
                "INSERT INTO usage VALUES (?,?,?,?,?,?,?)",
                (ts or time.time(), principal, session, kind, name, event, detail),
            )
        )

    def summary(self, principal: str) -> dict[str, dict]:
        """Per item name: kind, a count per event, and ``last_used``, the
        last read, invocation or run (None when never)."""
        rows = self._query(
            "SELECT item_name, item_kind, event, count(*), max(ts) FROM usage"
            " WHERE principal = ? GROUP BY item_name, item_kind, event",
            (principal,),
        )
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
        got = self._query(sql + " ORDER BY ts, rowid", args)
        keys = ("ts", "session", "kind", "name", "event", "detail")
        return [dict(zip(keys, r)) for r in got]

    def raised(self, principal: str) -> dict[str, list[str]]:
        """The flags last kept for each of ``principal``'s items."""
        out: dict[str, list[str]] = {}
        for name, flag in self._query(
            "SELECT item_name, flag FROM flags WHERE principal = ? ORDER BY rowid",
            (principal,),
        ):
            out.setdefault(name, []).append(flag)
        return out

    def keep_flags(self, principal: str, name: str, flags: list[str]) -> None:
        """Queue replacing an item's kept flags; returns at once."""
        self._q.put(
            (
                "sql",
                "DELETE FROM flags WHERE principal = ? AND item_name = ?",
                (principal, name),
            )
        )
        for f in flags:
            self._q.put(
                (
                    "sql",
                    "INSERT OR IGNORE INTO flags VALUES (?,?,?)",
                    (principal, name, f),
                )
            )

    def prune(self, now: float | None = None) -> None:
        """Queue dropping the rows older than a year, then VACUUM."""
        self._q.put(
            ("sql", "DELETE FROM usage WHERE ts < ?", ((now or time.time()) - KEEP_S,))
        )
