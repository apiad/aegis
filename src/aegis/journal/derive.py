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

from ..claude.stream import Echo, Result, ToolCall, ToolOutput
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


class Deriver:
    def __init__(self, handle: str = "") -> None:
        self.handle = handle
        self.cwd = ""
        self._parsers: dict = {}
        self._calls: dict[str, ToolCall] = {}
        self._last_call: str | None = None
        self._turn: list[tuple[str, str]] = []
        self._prompt = ""
        self._said: str | None = None
        self._done: set[str] = set()  # done in the previous plan record
        self._ts = 0.0

    def feed(self, record: dict, events: list | None = None) -> list[Row]:
        i = record["i"]
        ts = record.get("ts") or self._ts
        self._ts = ts
        src = record.get("src")
        if src in PARSERS:
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
            if call is None or ev.is_error:
                return []
            return self._settled(i, ts, call, ev.text, call.parent or call.id)
        if isinstance(ev, Echo):
            if not self._prompt:
                self._prompt = ev.text
            return []
        if isinstance(ev, Result):
            return self._close_turn(i, ts, id)
        return []

    def _settled(
        self, i: int, ts: float, call: ToolCall, text: str, source: str
    ) -> list[Row]:
        if call.name in WRITE_TOOLS:
            p = call.input.get("file_path") or call.input.get("notebook_path")
            if isinstance(p, str) and p:
                self._turn.append((WRITE_TOOLS[call.name], self._abs(p)))
            return []
        if call.name != "Bash":
            return []
        cmd = str(call.input.get("command") or "")
        cwd = self.cwd or os.sep
        rows = [
            self._row(
                i,
                ts,
                "commit",
                f"{h[:7]} {subject} · {branch}",
                source=source,
                commit=(shell.workdir(cmd, cwd), h),
            )
            for branch, h, subject in shell.commits(cmd, text)
        ]
        if pr := shell.pull_request(cmd, text):
            rows.append(self._row(i, ts, "pr", pr, source=source))
        self._turn += [("bash-write", p) for p in shell.writes(cmd, cwd)]
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
        said, prompt = self._said, self._prompt
        self._turn, self._said, self._prompt = [], None, ""
        self._last_call = None
        if not touches and not said:
            return []
        text = said or prompt[:120] or "a turn"
        return [self._row(i, ts, "turn", text, source=source, touches=touches)]
