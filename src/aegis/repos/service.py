"""Each session's repos: the ``repos:<log_id>`` channel behind the panel's row.

Rules:
- The journal says which worktrees, files, commits and PRs are a session's
  (its ``top`` and ``sha`` columns); live writes the writer thread reports
  (``Journal.on_live``) count at once, before the turn's row exists. git and
  gh say only the present state.
- Uncommitted counts only the files the session wrote; the rest of a shared
  checkout's dirt is ``others_n``.
- A worktree is probed at most once per ``PROBE_EVERY`` seconds: a request
  inside the window runs once at its end. Only sessions a browser subscribes
  to are built, polled (every ``POLL_EVERY``) and published, at most once per
  ``PUBLISH_EVERY`` and only when what they would send changed.
- No git, gh or SQLite on the event loop: builds run in a thread.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..journal import db, paths
from . import gh, probe

log = logging.getLogger(__name__)

PROBE_EVERY = 10.0
PUBLISH_EVERY = 5.0
POLL_EVERY = 60.0
WRITES = ("write", "edit", "bash-write")
PR_URL = re.compile(r"https://github\.com/[^/\s]+/[^/\s]+/pull/(\d+)")
PR_MERGED = re.compile(r"merged #(\d+)")


def channel(log_id: str) -> str:
    return f"repos:{log_id}"


class Repos:
    def __init__(
        self,
        db_path: Path,
        publish: Callable[[str, list[dict]], None],
        open_sessions: Callable[[], dict[str, str]],
        subscribers: Callable[[str], int],
        root: Path,
    ) -> None:
        self.db_path = db_path
        self._publish = publish
        self._open = open_sessions
        self._subscribers = subscribers
        self.root = str(root)
        self._loop: asyncio.AbstractEventLoop | None = None
        # log_id -> top -> (main checkout, newest ts, files written there)
        self._live: dict[str, dict[str, tuple[str, float, set[str]]]] = {}
        self._probes: dict[str, probe.Probe | None] = {}
        self._probed_at: dict[str, float] = {}
        self._probe_due: dict[str, asyncio.TimerHandle] = {}
        self._sent: dict[str, dict] = {}
        self._tops: dict[str, set[str]] = {}  # log_id -> tops in its last build
        self._built_at: dict[str, float] = {}
        self._build_due: dict[str, asyncio.TimerHandle] = {}
        self._building: set[str] = set()
        self._poll: asyncio.Task | None = None

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._poll = self._loop.create_task(self._polling())

    async def stop(self) -> None:
        if self._poll is not None:
            self._poll.cancel()
            self._poll = None
        for h in [*self._probe_due.values(), *self._build_due.values()]:
            h.cancel()

    async def _polling(self) -> None:
        while True:
            await asyncio.sleep(POLL_EVERY)
            for log_id in self._watched():
                for top in self._tops.get(log_id, ()):
                    self.request(top)

    def _watched(self) -> list[str]:
        return [k for k in list(self._sent) if self._subscribers(channel(k)) > 0]

    # -- the channel -------------------------------------------------------
    def snapshot(self, log_id: str) -> dict:
        """What a new subscriber gets now: the last build, or a loading mark
        while the first one runs."""
        if self._loop is not None:
            self._loop.call_soon(self.refresh, log_id, True)
        return self._sent.get(log_id) or {"repos": None}

    def refresh(self, log_id: str, now: bool = False) -> None:
        """Build and publish ``log_id``'s list, at most once per PUBLISH_EVERY."""
        if self._loop is None or log_id in self._build_due:
            return
        wait = self._built_at.get(log_id, -1e9) + PUBLISH_EVERY - time.monotonic()
        if now and log_id not in self._sent:
            wait = 0
        if wait > 0:
            self._build_due[log_id] = self._loop.call_later(wait, self._due, log_id)
            return
        self._due(log_id)

    def _due(self, log_id: str) -> None:
        loop = self._loop
        assert loop is not None  # scheduled only once started
        self._build_due.pop(log_id, None)
        if log_id in self._building:
            self._build_due[log_id] = loop.call_later(1.0, self._due, log_id)
            return
        self._building.add(log_id)
        self._built_at[log_id] = time.monotonic()
        loop.create_task(self._build_and_send(log_id))

    async def _build_and_send(self, log_id: str) -> None:
        try:
            data = await asyncio.to_thread(self.build, log_id)
        except Exception:
            log.exception("repos: building %s failed", log_id)
            return
        finally:
            self._building.discard(log_id)
        self._tops[log_id] = {r["top"] for r in data["repos"]}
        if self._sent.get(log_id) != data:
            self._sent[log_id] = data
            self._publish(channel(log_id), [{"set": data}])

    # -- probes --------------------------------------------------------------
    def request(self, top: str) -> None:
        """Probe ``top`` now, or at the end of its PROBE_EVERY window."""
        if self._loop is None or top in self._probe_due:
            return
        wait = self._probed_at.get(top, -1e9) + PROBE_EVERY - time.monotonic()
        self._probe_due[top] = self._loop.call_later(max(wait, 0), self._run_probe, top)

    def _run_probe(self, top: str) -> None:
        self._probe_due.pop(top, None)
        self._probed_at[top] = time.monotonic()
        assert self._loop is not None  # scheduled only once started
        self._loop.create_task(self._probe_and_refresh(top))

    async def _probe_and_refresh(self, top: str) -> None:
        self._probes[top] = await asyncio.to_thread(probe.probe, top)
        for log_id in self._watched():
            if top in self._tops.get(log_id, ()) or log_id in self._live:
                self.refresh(log_id)

    # -- live writes (from the journal's writer thread) ---------------------
    def live(
        self, log_id: str, handle: str, written: list[str], dirs: list[str]
    ) -> None:
        if self._loop is None:
            return
        places = [(p, *paths.place(p)) for p in written]
        tops = [paths.place(d)[2] for d in dirs]
        self._loop.call_soon_threadsafe(self._on_live, log_id, places, tops)

    def _on_live(self, log_id: str, places: list, tops: list[str | None]) -> None:
        mine = self._live.setdefault(log_id, {})
        now = time.time()
        for _, main, rel, top in places:
            if top is None:
                continue
            _, _, files = mine.get(top, (main, now, set()))
            files.add(rel)
            mine[top] = (main, now, files)
            self.request(top)
        for top in tops:
            if top is not None:
                gh.forget(top)
                self.request(top)
        # The probe's end rebuilds the sessions that list the top
        # (_probe_and_refresh): a rebuild here would probe the same top again.

    # -- building one session's list (in a thread) --------------------------
    def build(self, log_id: str) -> dict:
        con = db.open_read(self.db_path) if self.db_path.exists() else None
        try:
            return self._build(con, log_id)
        finally:
            if con is not None:
                con.close()

    def _build(self, con, log_id: str) -> dict:
        repos: dict[str, dict[str, Any]] = {}

        def at(top: str, main: str, ts: float) -> dict:
            r: dict[str, Any] | None = repos.get(top)
            if r is None:
                r = repos[top] = {
                    "top": top,
                    "main": main,
                    "ts": ts,
                    "own": set(),
                    "commits": [],
                    "prs": {},
                }
            r["ts"] = max(r["ts"], ts)
            return r

        if con is not None:
            for top, main, rel, op, ts in con.execute(
                "SELECT t.top, t.repo, t.path, t.op, e.ts FROM touches t"
                " JOIN entries e ON e.id = t.entry_id"
                " WHERE e.log_id = ? AND t.top IS NOT NULL AND t.op IN (?,?,?)",
                (log_id, *WRITES),
            ):
                at(top, main, ts)["own"].add(rel)
            for top, main, sha, text, ts in con.execute(
                "SELECT top, repo, sha, text, ts FROM entries WHERE log_id = ?"
                " AND kind = 'commit' AND top IS NOT NULL ORDER BY ts, id",
                (log_id,),
            ):
                at(top, main, ts)["commits"].append((sha, text))
            for top, main, text, ts in con.execute(
                "SELECT top, repo, text, ts FROM entries WHERE log_id = ?"
                " AND kind = 'pr' AND top IS NOT NULL ORDER BY ts, id",
                (log_id,),
            ):
                r = at(top, main, ts)
                if m := PR_URL.search(text):
                    title = text.split(" · ", 1)[1] if " · " in text else ""
                    r["prs"].setdefault(
                        int(m[1]),
                        {
                            "n": int(m[1]),
                            "url": m[0],
                            "title": title,
                            "state": "open",
                            "ci": "",
                        },
                    )
                elif m := PR_MERGED.search(text):
                    pr = r["prs"].setdefault(
                        int(m[1]),
                        {"n": int(m[1]), "url": "", "title": "", "state": "", "ci": ""},
                    )
                    pr["state"] = "merged"
        for top, (main, ts, files) in self._live.get(log_id, {}).items():
            at(top, main, ts)["own"] |= files

        gh_ok = True
        out = []
        for r in sorted(repos.values(), key=lambda r: -r["ts"]):
            line, ok = self._line(r)
            gh_ok &= ok
            out.append(line)
        self._peers(con, log_id, out)
        return {"repos": out, "gh": gh_ok}

    def _line(self, r: dict) -> tuple[dict, bool]:
        top, main = r["top"], r["main"]
        exists = os.path.isdir(top)
        if top not in self._probes and exists:
            self._probes[top] = probe.probe(top)
            self._probed_at[top] = time.monotonic()
        p = self._probes.get(top) if exists else None
        rel = os.path.relpath(top, self.root)
        line = {
            "name": os.path.basename(main),
            "top": top,
            "main": main,
            "path": top if rel.startswith("..") else rel,
            "worktree": top != main,
            "removed": not exists,
            "stale": exists and p is None,
            "branch": p.branch if p else "",
            "upstream": p.upstream if p else "",
            "op": p.op if p else "",
        }
        dirty = p.dirty if p else {}
        own = sorted(f for f in dirty if f in r["own"])
        line["dirty"] = [{"path": f, "stat": dirty[f]} for f in own[:10]]
        line["dirty_n"] = len(own)
        line["others_n"] = len(dirty) - len(own)

        commits = [(sha, *_subject(text)) for sha, text in r["commits"]]
        branches = {b for _, _, _, b in commits if b}
        if p and p.branch and (p.branch in branches or r["prs"]):
            branches.add(p.branch)
        ok = True
        prs = r["prs"]
        merged_branches = set()
        for b in sorted(branches):
            found = gh.prs(top, b) if exists else None
            if found is None:
                ok = False
                continue
            for pr in found:
                prs[pr["n"]] = {**prs.get(pr["n"], {}), **pr}
                if pr["state"] == "merged":
                    merged_branches.add(b)
        shas = [s for s, _, _, b in commits if s and b not in merged_branches]
        base = probe.base(top, p.upstream) if p else ""
        left = set(probe.unpushed(top, shas, base)) if p else set()
        unpushed = [subj for s, _, subj, _ in reversed(commits) if s in left]
        line["unpushed"] = unpushed[:5]
        line["unpushed_n"] = len(unpushed)
        line["left"] = [
            {"hash": h, "subject": subj} for _, h, subj, _ in reversed(commits[-5:])
        ]
        line["left_n"] = len(commits)
        line["prs"] = sorted(prs.values(), key=lambda pr: -pr["n"])
        line["clean"] = exists and p is not None and not own and not unpushed
        line["ts"] = r["ts"]
        return line, ok

    def _peers(self, con, log_id: str, lines: list[dict]) -> None:
        """Name, on each line, the other open sessions that wrote in the same
        repo, any worktree of it."""
        for ln in lines:
            ln["peers"] = []
        others = {k: h for k, h in self._open().items() if k != log_id}
        wanted = {ln["main"] for ln in lines}
        if not others or not wanted:
            return
        found: set[tuple[str, str, str]] = set()
        if con is not None:
            marks, mk = ",".join("?" * len(others)), ",".join("?" * len(wanted))
            found |= set(
                con.execute(
                    "SELECT DISTINCT e.log_id, t.top, t.repo FROM touches t"
                    " JOIN entries e ON e.id = t.entry_id"
                    f" WHERE e.log_id IN ({marks}) AND t.repo IN ({mk})"
                    " AND t.top IS NOT NULL AND t.op IN (?,?,?)",
                    (*others, *wanted, *WRITES),
                )
            )
        for other in others:
            for top, (main, _, _) in self._live.get(other, {}).items():
                if main in wanted:
                    found.add((other, top, main))
        for other, top, main in sorted(found):
            p = self._probes.get(top)
            where = (
                "main checkout" if top == main else f"worktree {os.path.basename(top)}"
            )
            if p and p.branch:
                where += f", {p.branch}"
            peer = {"handle": others[other], "where": where}
            for ln in lines:
                if ln["main"] == main and peer not in ln["peers"]:
                    ln["peers"].append(peer)


def _subject(text: str) -> tuple[str, str, str]:
    """(short hash, subject, branch) from a commit row's text."""
    h, _, rest = text.partition(" ")
    subject, _, branch = rest.rpartition(" · ")
    return h, (subject or rest), branch
