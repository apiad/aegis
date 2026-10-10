"""Derive journal rows from one session's store records.

The Deriver is the only place that decides what the journal says, and it is
pure. It reads the records the fold reads, in order, and returns the rows each
one makes. The live path and a backfill feed it the same records and get the
same rows; that is what lets the journal be rebuilt at all. Paths stay absolute
here: the index names them by repo on the writer thread (paths.py).

A store written before the spawn record carried a handle takes the handle the
caller passes, which is the meta's.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from ..claude.stream import (
    CommandEcho,
    Echo,
    Init,
    Notice,
    Result,
    Text,
    ToolCall,
    ToolOutput,
)
from ..transcript.entries import PARSERS
from . import shell

WRITE_TOOLS = {
    "Write": "write",
    "Edit": "edit",
    "MultiEdit": "edit",
    "NotebookEdit": "edit",
    "Delete": "write",
}
TURN_ENDS = ("exit", "stop", "server_stopped", "reset")


@dataclass
class Row:
    rec: int
    ts: float
    kind: str
    text: str
    handle: str
    tag: str = ""
    source: str | None = None
    touches: list[tuple[str, str]] = field(default_factory=list)
    commit: tuple[str, str] | None = None
    # Where else a commit's hash may be: the directories the turn wrote in
    # before it, tried when the guessed directory does not hold the hash.
    near: tuple[str, ...] = ()


class Deriver:
    def __init__(self, handle: str = "") -> None:
        self.handle = handle
        self.cwd = ""
        self._parsers: dict = {}
        self._calls: dict[str, ToolCall] = {}
        self._last_call: str | None = None
        self._turn: list[tuple[str, str]] = []
        self._prompt = ""
        # What names a turn that has no prompt (a wake): its first prose line,
        # else a background task's notice. OpenCode updates a part in place, so
        # the part's latest text stands.
        self._prose = ""
        self._prose_key: str | None = None
        self._notice = ""
        # A turn_end line names the turn before its prompt: done or review
        # first, else needs_you, whose prompt is often a one-word reply.
        self._said: str | None = None
        self._asked: str | None = None
        self._done: set[str] = set()  # done in the previous plan record
        self._ts = 0.0
        self._src = ""

    def feed(self, record: dict, events: list | None = None) -> list[Row]:
        i = record["i"]
        ts = record.get("ts") or self._ts
        self._ts = ts
        src = record.get("src")
        if src in PARSERS:
            self._src = src
            if events is None:
                p = self._parsers.get(src)
                if p is None:
                    p = self._parsers[src] = PARSERS[src]()
                events = p.feed(record.get("line", ""))
            rows: list[Row] = []
            for k, ev in enumerate(events):
                rows += self._event(i, f"e{i}.{k}", ts, ev)
            return rows
        return self._own(i, ts, record)

    def _row(self, i: int, ts: float, kind: str, text: str, **kw) -> Row:
        return Row(i, ts, kind, text, self.handle, **kw)

    def _abs(self, p: str) -> str:
        return os.path.normpath(os.path.join(self.cwd or os.sep, os.path.expanduser(p)))

    def _event(self, i: int, id: str, ts: float, ev) -> list[Row]:
        if isinstance(ev, ToolCall):
            self._calls[ev.id] = ev
            if ev.parent is None:
                self._last_call = ev.id
            return []
        if isinstance(ev, ToolOutput):
            call = self._calls.pop(ev.id, None)
            if call is None:
                return []
            return self._settled(i, ts, call, ev, call.parent or call.id)
        if isinstance(ev, Init):
            if ev.cwd:
                self.cwd = ev.cwd
            return []
        if isinstance(ev, Echo):
            if not self._prompt:
                self._prompt = ev.text
            return []
        if isinstance(ev, CommandEcho):
            if not self._prompt:
                self._prompt = f"/{ev.name} {ev.args}".strip()
            return []
        if isinstance(ev, Text):
            line = next((x.strip() for x in ev.text.splitlines() if x.strip()), "")
            first = not self._prose and ev.parent is None
            if line and (first or (ev.key and ev.key == self._prose_key)):
                self._prose, self._prose_key = line, ev.key
            return []
        if isinstance(ev, Notice):
            if ev.subtype == "task_notification" and not self._notice:
                self._notice = f"a background task {ev.status or 'ended'}"
            return []
        if isinstance(ev, Result):
            return self._close_turn(i, ts, id)
        return []

    def _settled(
        self, i: int, ts: float, call: ToolCall, out: ToolOutput, source: str
    ) -> list[Row]:
        if call.name in WRITE_TOOLS:
            p = call.input.get("file_path") or call.input.get("notebook_path")
            if isinstance(p, str) and p and not out.is_error:
                self._turn.append((WRITE_TOOLS[call.name], self._abs(p)))
            return []
        if call.name != "Bash":
            return []
        # A Bash call that failed may still have committed or merged first
        # (a rejected push after the commit, gh pr merge in a worktree).
        cmd, text = str(call.input.get("command") or ""), out.text
        cwd = self.cwd or os.sep
        found = shell.commits(cmd, text)
        near = tuple(
            dict.fromkeys(
                os.path.dirname(p) for op, p in self._turn if op in ("write", "edit")
            )
        )
        rows = [
            self._row(
                i,
                ts,
                "commit",
                f"{h[:7]} {subject} · {branch}",
                source=source,
                commit=(d, h),
                near=near,
            )
            for (branch, h, subject), d in zip(
                found, shell.commit_dirs(cmd, cwd, len(found))
            )
        ]
        if pr := shell.pull_request(cmd, text, out.is_error):
            rows.append(self._row(i, ts, "pr", pr, source=source))
        if not out.is_error:
            self._turn += [("bash-write", p) for p in shell.writes(cmd, cwd)]
        # Claude's Bash keeps its directory between the agent's own calls; a
        # subagent's calls start afresh each time.
        if self._src == "claude" and call.parent is None:
            self.cwd = shell.leading_cd(cmd, cwd) or self.cwd
        return rows

    def _own(self, i: int, ts: float, rec: dict) -> list[Row]:
        kind = rec.get("kind")
        if kind == "spawn":
            self.cwd = str(rec.get("cwd") or "")
            self.handle = str(rec.get("handle") or self.handle)
            by = rec.get("spawned_by")
            by = by.get("handle") if isinstance(by, dict) else by
            text = f"spawned in {self.cwd}" + (f" by {by}" if by else "")
            return [self._row(i, ts, "session", text, source=f"e{i}")]
        if kind == "name":
            old, new = self.handle, str(rec.get("handle") or "")
            if new:
                self.handle = new
            if old and new and new != old:
                return [self._row(i, ts, "session", f"renamed from {old} to {new}")]
            return []
        if kind == "send":
            if not self._prompt:
                self._prompt = str(rec.get("text") or "")
            return []
        if kind == "turn_end":
            if rec.get("attention") in ("done", "review"):
                self._said = str(rec.get("line") or "") or None
            elif rec.get("attention") == "needs_you":
                self._asked = str(rec.get("line") or "") or None
            return []
        if kind == "plan":
            now = [
                str(item.get("text") or "")
                for item in rec.get("items") or []
                if item.get("state") == "done"
            ]
            rows = [
                self._row(i, ts, "plan", text, source=self._last_call)
                for text in dict.fromkeys(now)
                if text and text not in self._done
            ]
            self._done = set(now)
            return rows
        if kind == "journal_note":
            touches = [
                ("note", self._abs(p))
                for p in rec.get("paths") or []
                if isinstance(p, str) and p
            ]
            return [
                self._row(
                    i,
                    ts,
                    "note",
                    str(rec.get("text") or ""),
                    tag=str(rec.get("tag") or ""),
                    source=self._last_call,
                    touches=touches,
                )
            ]
        if kind in TURN_ENDS:
            self._end_turn()
            return self._close_turn(i, ts, f"e{i}")
        if kind == "close":
            self._end_turn()
            return self._close_turn(i, ts, f"e{i}") + [
                self._row(i, ts, "session", "closed", source=f"e{i}")
            ]
        return []

    def _end_turn(self) -> None:
        """A turn ended without a result: each parser forgets it, as the Fold's
        do, so a rebuild sees the events the live Fold saw."""
        for p in self._parsers.values():
            end = getattr(p, "end_turn", None)
            if end is not None:
                end()
        self._calls.clear()

    def _close_turn(self, i: int, ts: float, source: str) -> list[Row]:
        touches = list(dict.fromkeys(self._turn))
        said, asked, prompt = self._said, self._asked, self._prompt
        named = prompt or self._prose or self._notice or "a turn"
        self._turn, self._said, self._asked, self._prompt = [], None, None, ""
        self._prose, self._prose_key, self._notice = "", None, ""
        self._last_call = None
        if not touches and not said:
            return []
        text = said or asked or named[:120]
        return [self._row(i, ts, "turn", text, source=source, touches=touches)]
