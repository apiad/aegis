"""The journal: what was done on this server, derived from the transcript stores.

Rules:
- Rows come only from a Deriver over store records, live (``recorded``) and in
  a backfill. Both insert rows keyed (log_id, record, n), so they insert the
  same rows and a repeat does nothing (db.py).
- Everything that runs git or SQLite runs on one writer thread. The event loop
  only enqueues, so a commit's ``git show`` never stalls a keystroke.
- Boot reads no store. A journal.db whose schema is not this one's is reset and
  backfilled on the writer thread after boot; until then searches see less.
- A backfill that did not finish (a stop, a crash) is not marked complete, so
  the next start runs it again. The mark is the file's ``application_id``;
  db.py's tables stay as they are, and every insert is idempotent. Each
  backfill run writes its own token there when it begins (a rebuild, in the
  transaction that clears), and marks the file complete only if the token is
  still its own: a rebuild that cleared meanwhile leaves the file incomplete.
- The journal is derived data: a file SQLite cannot read is set aside as
  journal.db.corrupt-<time> and recreated, never a reason to fail boot.
- A session's first record in a server run primes its Deriver from the store,
  on the writer thread, so a live row equals the row a rebuild makes.
- The usage deriver (usage/turns.py) is fed the same records beside the
  journal's Deriver, live and in its own backfill. Its rows are written in
  their own transaction right after the journal's, and the usage tables keep
  their own version and backfill mark (usage/tables.py), so a complete journal
  that gains them backfills usage only. Each write publishes the ``usage``
  channel with the time and how far that backfill has come.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import queue
import random
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path

from ..quota.core import PACE_HISTORY_LONG, PACE_HISTORY_SHORT
from ..transcript.store import read_store
from ..usage import tables
from ..usage.context import successor_start
from ..usage.owners import owner_of
from ..usage.store import sessions as stored_sessions
from ..usage.turns import Derived, SessionInfo, UsageDeriver
from . import db, paths
from .derive import Deriver, Row

log = logging.getLogger(__name__)

# PRAGMA application_id of a file whose backfill finished ("JRNL").
COMPLETE = 0x4A524E4C


def _mark(con, value: int) -> None:
    con.execute(f"PRAGMA application_id={value}")


def _token() -> int:
    """A nonzero application_id no other run shares, and never COMPLETE."""
    while (t := random.randrange(1, 0x7FFFFFFF)) == COMPLETE:
        pass
    return t


def _scratch(directory: str) -> bool:
    """A scratch directory inside a repo names none (spec 06); one outside
    every repo, such as /tmp, names none through paths.place."""
    return "/.playground/" in directory.rstrip("/") + "/"


def _kind(meta: dict) -> str:
    for key, kind in (
        ("worker", "worker"),
        ("workflow", "workflow"),
        ("schedule", "schedule"),
    ):
        if meta.get(key):
            return kind
    return "foreground"


def _complete_if(con, token: int) -> None:
    con.execute("BEGIN IMMEDIATE")
    try:
        if con.execute("PRAGMA application_id").fetchone()[0] == token:
            _mark(con, COMPLETE)
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise


class Journal:
    def __init__(
        self,
        state_root: Path,
        publish,
        db_path: Path | None = None,
        on_live=None,
        user_of=lambda log_id: None,
        meta_of: Callable[[str], dict] = lambda log_id: {},
        accounts: Callable[[], dict[str, str]] = dict,
    ) -> None:
        self.state_root = state_root
        # The Linux user of a session by log id: its git runs as that user.
        self.user_of = user_of
        # Called on the writer thread with (log_id, handle, new paths, dirs)
        # when a live record wrote files, or committed, opened or merged a PR
        # or pushed in dirs: the Repos row must see a write before the turn
        # that made it closes.
        self.on_live = on_live
        self._seen: dict[str, int] = {}
        self.path = db_path or state_root / "journal.db"
        self._publish = publish
        self._derivers: dict[str, Deriver] = {}
        # A session's meta (owner, kind, harness, continues) and the account
        # each harness is logged in as, read when its usage deriver is made.
        self.meta_of = meta_of
        self.accounts = accounts
        self._usage: dict[str, UsageDeriver] = {}
        self._start: tuple[float, int] | None = None  # (read at, replay start)
        # A directory's repo, and the repos whose owner has been read.
        self._repos: dict[tuple[str, str | None], str | None] = {}
        self._owned: set[str] = set()
        self._progress: dict = {}
        self._usage_at = 0.0
        self._published = 0.0
        # A --repo slug's checkout, found once per process (writer thread).
        self._slugs: dict[str, str] = {}
        self._q: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._at = 0.0
        self._halt = threading.Event()
        self.fresh = False

    # -- lifecycle -------------------------------------------------------
    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._halt.clear()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con, self.fresh = self._open()
        if self.fresh:
            _mark(con, 0)
        done = con.execute("PRAGMA application_id").fetchone()[0] == COMPLETE
        tables.ensure(con)
        usage_done = tables.is_complete(con)
        con.close()
        self._thread = threading.Thread(target=self._write, name="journal", daemon=True)
        self._thread.start()
        if not done:
            self._q.put(("backfill",))
        if not usage_done:
            self._progress = {"done": 0, "total": 0}
            self._q.put(("usage_backfill",))

    def _open(self):
        try:
            return db.connect(self.path)
        except sqlite3.OperationalError:
            raise  # busy or locked: the file may be fine
        except sqlite3.DatabaseError as err:
            aside = self.path.with_name(f"{self.path.name}.corrupt-{int(time.time())}")
            log.warning(
                "journal: %s is not a database (%s); set aside as %s",
                self.path,
                err,
                aside,
            )
            self.path.replace(aside)
            for ext in ("-wal", "-shm"):
                Path(f"{self.path}{ext}").unlink(missing_ok=True)
            return db.connect(self.path)

    def stop(self) -> None:
        """End the writer thread. A running backfill stops between sessions."""
        if self._thread is not None:
            self._halt.set()
            self._q.put(None)
            self._thread.join(timeout=10)
            self._thread = None

    def flush(self) -> None:
        """Wait until everything queued is written (tests, the CLI). Returns
        at once if the writer thread is gone."""
        t = self._thread
        if t is None:
            return
        done = threading.Event()
        self._q.put(("sync", done))
        while not done.wait(0.1):
            if not t.is_alive():
                return

    def run(self, fn: Callable) -> concurrent.futures.Future:
        """``fn(con)`` on the writer thread, as every write runs; its result
        or error lands in the Future. The usage tables changed after it."""
        fut: concurrent.futures.Future = concurrent.futures.Future()
        if self._thread is None or not self._thread.is_alive():
            fut.set_exception(RuntimeError("the journal is not running"))
        else:
            self._q.put(("run", fn, fut))
        return fut

    def snapshot(self) -> dict:
        return {"at": self._at}

    def usage_snapshot(self) -> dict:
        return {"at": self._usage_at, "backfill": self.usage_progress()}

    def usage_progress(self) -> dict:
        """How far the usage backfill has come, or {} when it is complete."""
        return dict(self._progress)

    # -- feeding -----------------------------------------------------------
    def recorded(
        self,
        log_id: str,
        handle: str,
        store_path: Path,
        record: dict,
        events: list | None,
    ) -> None:
        if self._thread is not None and self._thread.is_alive():
            self._q.put(("feed", log_id, handle, store_path, record, events))

    def rebuild(self) -> int:
        """Empty the file and derive it again from every store. Synchronous.
        A file SQLite cannot read is set aside first, as at start."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con, _ = self._open()
        try:
            token = _token()
            db.clear(con, mark=token)
            n = self._backfill(con, token)
            tables.ensure(con)
            token = _token()
            tables.clear(con, token)
            self._usage_backfill(con, token)
            return n
        finally:
            con.close()

    # -- the writer thread ---------------------------------------------------
    def _write(self) -> None:
        try:
            con, _ = db.connect(self.path)
        except Exception:
            log.exception("journal: cannot open %s", self.path)
            return
        while True:
            item = self._q.get()
            try:
                if item is None:
                    con.close()
                    return
                if item[0] == "sync":
                    item[1].set()
                    continue
                if item[0] == "usage_backfill":
                    self._usage_backfill(con)
                    continue
                if item[0] == "run":
                    try:
                        item[2].set_result(item[1](con))
                    except Exception as e:
                        item[2].set_exception(e)
                    self._usage_changed()
                    continue
                if item[0] == "quota":
                    tables.quota(con, item[1])
                    continue
                n = (
                    self._backfill(con)
                    if item[0] == "backfill"
                    else self._feed(con, *item[1:])
                )
                if n:
                    self._changed()
            except Exception:
                log.exception("journal: %s failed", item[0])

    def _feed(self, con, log_id, handle, store_path, record, events) -> int:
        d = self._derivers.get(log_id)
        u = self._usage.get(log_id)
        if d is None or u is None:
            before = []
            for r in read_store(store_path)[0]:
                if r["i"] >= record["i"]:
                    break
                before.append(r)
            if d is None:
                d = Deriver(handle)
                for r in before:
                    d.feed(r)
                self._derivers[log_id] = d
            if u is None:
                try:
                    u = self._usage_deriver(con, log_id)
                    for r in before:
                        u.feed(r)
                    self._usage[log_id] = u
                except Exception:
                    log.exception("journal: priming usage of %s failed", log_id)
                    u = None
        rows = d.feed(record, events)
        n = self._insert(con, log_id, rows)
        if u is not None:
            try:
                if self._usage_insert(con, log_id, u.feed(record, events)):
                    self._usage_changed()
            except Exception:
                log.exception("journal: usage of %s failed", log_id)
        if self.on_live is not None:
            pending = d.pending()
            fresh = pending[self._seen.get(log_id, 0) :]
            self._seen[log_id] = len(pending)
            dirs = [r.commit[0] for r in rows if r.commit]
            dirs += [r.workdir for r in rows if r.workdir]
            dirs += d.take_pushes()
            if fresh or dirs:
                try:
                    self.on_live(log_id, d.handle, fresh, list(dict.fromkeys(dirs)))
                except Exception:
                    log.exception("journal: on_live failed")
        return n

    def _backfill(self, con, token: int | None = None) -> int:
        if token is None:
            token = _token()
            _mark(con, token)
        n = 0
        for s in stored_sessions(self.state_root):
            if self._halt.is_set():
                return n
            try:
                d = Deriver(s.handle or "")
                for r in read_store(s.path)[0]:
                    n += self._insert(con, s.log_id, d.feed(r))
            except Exception:
                log.exception("journal: backfill of %s failed", s.log_id)
        _complete_if(con, token)
        return n

    # -- usage ---------------------------------------------------------------
    def _usage_deriver(self, con, log_id: str) -> UsageDeriver:
        """A session's usage deriver, from its meta; a store with no meta
        derives with the defaults."""
        try:
            meta = self.meta_of(log_id) or {}
        except Exception:
            meta = {}
        info = SessionInfo(
            person=str(meta.get("owner") or ""),
            kind=_kind(meta),
            harness=str(meta.get("harness") or "claude-code"),
            continues=meta.get("continues") or None,
            account=lambda harness: self.accounts().get(harness),
            replay_start=self._replay_start(con),
        )
        return UsageDeriver(info, self._repo_of(con, self.user_of(log_id)))

    def _replay_start(self, con) -> int:
        """Successors' median start context, read at most once a minute: a
        backfill makes a deriver per session."""
        now = time.monotonic()
        if self._start is None or now - self._start[0] > 60:
            self._start = (now, successor_start(con))
        return self._start[1]

    def _repo_of(self, con, user: str | None) -> Callable[[str], str | None]:
        """A directory's main checkout, or a ``gh:owner/name`` slug's, cached
        per directory; a scratch directory names none."""

        def repo_of(d: str) -> str | None:
            key = (d, user)
            if key in self._repos:
                return self._repos[key]
            if d.startswith("gh:"):
                hit = self._checkout_of(con, d[3:], user)
                if hit is None:
                    return None  # a repo the journal first sees later may match
                repo = paths.place(hit, user)[0]
            else:
                repo = None if _scratch(d) else paths.place(d, user)[0]
            self._repos[key] = repo
            return repo

        return repo_of

    def _usage_insert(self, con, log_id: str, d: Derived) -> int:
        if not d:
            return 0
        n = tables.insert(con, log_id, d)
        user = self.user_of(log_id)
        for repo in {p.repo for p in d.parts if p.repo} - self._owned:
            self._owned.add(repo)
            if os.path.isdir(repo):
                owner_of(con, repo, repo, user, {})
        return n

    def _usage_backfill(self, con, token: int | None = None) -> int:
        if token is None:
            token = _token()
            tables.mark(con, token)
        total = sum(1 for _ in (self.state_root / "transcripts").glob("*.jsonl"))
        self._progress = {"done": 0, "total": total}
        n = 0
        for k, s in enumerate(stored_sessions(self.state_root)):
            if self._halt.is_set():
                return n
            try:
                u = self._usage_deriver(con, s.log_id)
                for r in read_store(s.path)[0]:
                    n += self._usage_insert(con, s.log_id, u.feed(r))
            except Exception:
                log.exception("journal: usage backfill of %s failed", s.log_id)
            self._progress["done"] = k + 1
            if time.monotonic() - self._published > 1:
                self._usage_changed()
        tables.complete_if(con, token)
        if tables.is_complete(con):
            self._progress = {}
        self._usage_changed()
        return n

    def quota(self, snapshot: dict) -> None:
        """A quota reading (Quota.listeners): kept on the writer thread when
        a window's percent or reset moved."""
        self._q.put(("quota", snapshot))

    def usual_spend(self, billing: str, window: str, span_s: float) -> float | None:
        """What the last finished windows of this kind spent (tables.usual_spend).
        None when the file or its table is not there yet, as before a backfill."""
        keep = PACE_HISTORY_SHORT if span_s < 86400 else PACE_HISTORY_LONG
        try:
            con = db.open_read(self.path)
            try:
                return tables.usual_spend(
                    con, billing, window, span_s, time.time(), keep
                )
            finally:
                con.close()
        except sqlite3.Error:
            return None

    def _usage_changed(self) -> None:
        self._usage_at = time.time()
        self._published = time.monotonic()
        if self._publish is not None and self._loop is not None:
            self._loop.call_soon_threadsafe(
                self._publish, "usage", [{"set": self.usage_snapshot()}]
            )

    def _insert(self, con, log_id: str, rows: list[Row]) -> int:
        return sum(
            db.insert(con, self._entry(log_id, n, row, con))
            for n, row in enumerate(rows)
        )

    def _entry(self, log_id: str, n: int, row: Row, con=None) -> db.Entry:
        user = self.user_of(log_id)
        touches = []
        for op, p in row.touches:
            repo, rel, top = paths.place(p, user)
            touches.append((op, repo, rel, paths.full(repo, rel), top))
        repo = touches[0][1] if touches else None
        top = touches[0][4] if touches else None
        sha = None
        unknown = False
        text = row.text
        if row.commit:
            # The guessed directory first, then where the turn wrote. A hash
            # found nowhere names no repo rather than a wrong one.
            directory, h = row.commit
            files = None
            for d in dict.fromkeys((directory, *row.near)):
                found = h
                if not h:  # it printed none: its subject is all there is
                    subject = row.text.partition(" ")[2]
                    found = paths.commit_by_subject(d, subject, row.ts, user)
                    if not found:
                        continue
                    text = f"{found[:7]} {subject}"
                    h = found
                files = paths.commit_files(d, found, user)
                if files is not None:
                    directory = d
                    break
            repo, _, top = (
                paths.place(directory, user) if files is not None else (None, "", None)
            )
            sha = paths.full_sha(directory, h, user) if files is not None else None
            unknown = files is None
            if not h:  # no ref holds it any more, but the command named its repo
                repo, _, top = paths.place(directory, user)
            touches += [
                ("commit", repo, f, paths.full(repo, f), top) for f in files or []
            ]
        workdir = row.workdir
        if row.slug and not workdir and con is not None:
            workdir = self._checkout_of(con, row.slug, user) or ""
        if workdir:
            repo, _, top = paths.place(workdir, user)
        return db.Entry(
            log_id,
            row.rec,
            n,
            row.ts,
            row.handle,
            repo,
            row.kind,
            row.tag,
            text,
            row.source,
            unknown,
            touches,
            top,
            sha,
        )

    def _checkout_of(self, con, slug: str, user: str | None) -> str | None:
        """The checkout of a --repo owner/name among the repos the journal
        has seen. Only a hit is kept: a repo first seen later may still match."""
        key = slug.lower()
        if key not in self._slugs:
            repos = [
                r
                for (r,) in con.execute(
                    "SELECT DISTINCT repo FROM entries WHERE repo IS NOT NULL"
                )
            ]
            hit = paths.checkout_of(slug, repos, user)
            if hit is None:
                return None
            self._slugs[key] = hit
        return self._slugs[key]

    def _changed(self) -> None:
        self._at = time.time()
        if self._publish is not None and self._loop is not None:
            self._loop.call_soon_threadsafe(
                self._publish, "journal", [{"set": self.snapshot()}]
            )
