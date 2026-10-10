"""The journal: what was done on this server, derived from the transcript stores.

Rules:
- Rows come only from a Deriver over store records, live (``recorded``) and in
  a backfill. Both insert rows keyed (log_id, record, n), so they insert the
  same rows and a repeat does nothing (db.py).
- Everything that runs git or SQLite runs on one writer thread. The event loop
  only enqueues, so a commit's ``git show`` never stalls a keystroke.
- Boot reads no store. A journal.db whose schema is not this one's is reset and
  backfilled on the writer thread after boot; until then searches see less.
- A session's first record in a server run primes its Deriver from the store,
  on the writer thread, so a live row equals the row a rebuild makes.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time
from pathlib import Path

from ..transcript.store import read_store
from ..usage.store import sessions as stored_sessions
from . import db, paths
from .derive import Deriver, Row

log = logging.getLogger(__name__)


class Journal:
    def __init__(self, state_root: Path, publish, db_path: Path | None = None) -> None:
        self.state_root = state_root
        self.path = db_path or state_root / "journal.db"
        self._publish = publish
        self._derivers: dict[str, Deriver] = {}
        self._q: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._at = 0.0
        self.fresh = False

    # -- lifecycle -------------------------------------------------------
    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con, self.fresh = db.connect(self.path)
        con.close()
        self._thread = threading.Thread(target=self._write, name="journal", daemon=True)
        self._thread.start()
        if self.fresh:
            self._q.put(("backfill",))

    def stop(self) -> None:
        if self._thread is not None:
            self._q.put(None)
            self._thread.join(timeout=10)
            self._thread = None

    def flush(self) -> None:
        """Wait until everything queued is written (tests, the CLI)."""
        self._q.join()

    def snapshot(self) -> dict:
        return {"at": self._at}

    # -- feeding -----------------------------------------------------------
    def recorded(
        self,
        log_id: str,
        handle: str,
        store_path: Path,
        record: dict,
        events: list | None,
    ) -> None:
        if self._thread is not None:
            self._q.put(("feed", log_id, handle, store_path, record, events))

    def rebuild(self) -> int:
        """Empty the file and derive it again from every store. Synchronous."""
        con, _ = db.connect(self.path)
        try:
            db.clear(con)
            return self._backfill(con)
        finally:
            con.close()

    # -- the writer thread ---------------------------------------------------
    def _write(self) -> None:
        con, _ = db.connect(self.path)
        while True:
            item = self._q.get()
            try:
                if item is None:
                    con.close()
                    return
                n = (
                    self._backfill(con)
                    if item[0] == "backfill"
                    else self._feed(con, *item[1:])
                )
                if n:
                    self._changed()
            except Exception:
                log.exception("journal: %s failed", item[0] if item else "stop")
            finally:
                self._q.task_done()

    def _feed(self, con, log_id, handle, store_path, record, events) -> int:
        d = self._derivers.get(log_id)
        if d is None:
            d = self._derivers[log_id] = Deriver(handle)
            for r in read_store(store_path)[0]:
                if r["i"] >= record["i"]:
                    break
                d.feed(r)
        return self._insert(con, log_id, d.feed(record, events))

    def _backfill(self, con) -> int:
        n = 0
        for s in stored_sessions(self.state_root):
            d = Deriver(s.handle or "")
            for r in read_store(s.path)[0]:
                n += self._insert(con, s.log_id, d.feed(r))
        return n

    def _insert(self, con, log_id: str, rows: list[Row]) -> int:
        return sum(
            db.insert(con, self._entry(log_id, n, row)) for n, row in enumerate(rows)
        )

    def _entry(self, log_id: str, n: int, row: Row) -> db.Entry:
        touches = []
        for op, p in row.touches:
            repo, rel = paths.name(p)
            touches.append((op, repo, rel, paths.full(repo, rel)))
        repo = touches[0][1] if touches else None
        unknown = False
        if row.commit:
            directory, h = row.commit
            repo = paths.name(directory)[0]
            files = paths.commit_files(directory, h)
            unknown = files is None
            touches += [("commit", repo, f, paths.full(repo, f)) for f in files or []]
        return db.Entry(
            log_id,
            row.rec,
            n,
            row.ts,
            row.handle,
            repo,
            row.kind,
            row.tag,
            row.text,
            row.source,
            unknown,
            touches,
        )

    def _changed(self) -> None:
        self._at = time.time()
        if self._publish is not None and self._loop is not None:
            self._loop.call_soon_threadsafe(
                self._publish, "journal", [{"set": self.snapshot()}]
            )
