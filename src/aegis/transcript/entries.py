"""Fold store records into transcript entries.

An entry is what the browser draws, with every decision already made here:
glyph, title, one-line summary, status, what collapses, the diff window. Ids
are deterministic (a tool entry is its ``tool_use_id``; anything else is
``e<record>.<block>``; a sent prompt is ``pending:<record>``), so folding the
same records twice gives the same entries, and the live session's entries
equal a fold of its store.

Two rules from Claude Code's measured behaviour live here:

- The echo creates the user entry, never the send. An answer takes the pending
  send whose text it answers, else the oldest of its kind: a prompt sent
  mid-turn is read at the next tool boundary, while a slash command waits for
  the turn to end, so the two kinds are answered out of order.
- System notices make no entry.
"""

from __future__ import annotations

import re
from collections import deque
from typing import Any

from ..claude.stream import (
    CommandEcho,
    CommandOutput,
    Compact,
    Delta,
    Echo,
    Event,
    Garbled,
    Init,
    LocalCommand,
    Reset,
    Result,
    Text,
    Thinking,
    ToolCall,
    ToolOutput,
    parse,
)
from .. import files
from ..artifacts import EVENTS_KEPT
from ..opencode.stream import Parser as OpenCodeParser
from . import describe as d
from .wire import wire


class _Stateless:
    """Claude's stream-json needs no memory between lines."""

    def feed(self, line: str) -> list[Event]:
        return parse(line)


# The store's src tag -> a parser factory. A fold keeps one parser per tag, so
# a harness whose events need earlier lines (OpenCode's) sees them in order.
PARSERS: dict[str, Any] = {"claude": _Stateless, "opencode": OpenCodeParser}


# From which fold level of the browser's view a kind folds: 1, the work between
# what was said; 2, also what arrived and what was shown along the way. A
# message of yours or the agent's (user, command, prose) never folds. The
# browser draws each run of consecutive folded entries as one line
# (js/transcript.js).
FOLD_LEVEL = {
    "tool": 1,
    "thinking": 1,
    "system": 1,
    "inbox": 2,
    "file": 2,
    "artifact": 2,
    "error": 2,
    "recap": 2,
}


def _entry(
    id: str,
    kind: str,
    status: str,
    ts: float | None,
    glyph: str,
    title: str = "",
    summary: str = "",
    md: str | None = None,
    detail: dict | None = None,
) -> dict:
    return {
        "id": id,
        "kind": kind,
        "status": status,
        "ts": ts,
        "glyph": glyph,
        "title": title,
        "summary": summary,
        "md": md,
        "detail": detail or {},
        "fold": FOLD_LEVEL.get(kind, 0),
    }


# What a session stands on between turns, for its card: the agent's plan, the
# item it finished last, its report on the turn that ended (turn_end), and why
# that turn failed. Replaced as a whole when it changes, so a session compares
# identity to know whether to publish.
EMPTY_STANDING: dict = {
    "plan": [],
    "did": "",
    "report": None,
    "turn_error": "",
    "last_message": "",
}


def _did(old: list[dict], new: list[dict], prev: str) -> str:
    """The item finished most recently: one that became done in this update, or
    the previous one while it is still done, or the last done item."""
    was = {i["text"] for i in old if i["state"] == "done"}
    fresh = [i["text"] for i in new if i["state"] == "done" and i["text"] not in was]
    if fresh:
        return fresh[-1]
    done = [i["text"] for i in new if i["state"] == "done"]
    if prev in done:
        return prev
    return done[-1] if done else ""


# The kinds a recap reads (recap._line).
CONTENT_KINDS = frozenset({"user", "prose", "tool", "inbox"})


def _sent_file(rec: dict) -> dict:
    """One sent file as its card shows it (files.store's record)."""
    url = files.url(str(rec.get("file_id")), str(rec.get("name")))
    return {
        "name": str(rec.get("name")),
        "summary": f"{files.human_size(int(rec.get('size') or 0))} · {rec.get('mime')}",
        "file_id": rec.get("file_id"),
        "url": url,
        "download": f"{url}?download=1",
        "preview": rec.get("preview"),
        "mime": rec.get("mime"),
        "size": rec.get("size"),
        "excerpt": rec.get("excerpt"),
    }


class Fold:
    def __init__(self) -> None:
        self._entries: dict[str, dict] = {}
        self._pending: deque[str] = deque()
        self._calls: dict[str, ToolCall] = {}
        self._seen_init = False
        self._interrupted = False
        self._last_cost = 0.0
        self._parsers: dict[str, Any] = {}
        # Entries only deltas made, whose part has not closed yet.
        self._live: set[str] = set()
        # The store index of the record being folded; stamped on every entry
        # it changes as ``rev`` (-1 before the first record).
        self._rev = -1
        # Removed entry ids, by the rev that removed them. Kept after an id
        # comes back, so a delta removes it first and appends it again, as a
        # fresh fold orders it.
        self._removed: dict[str, int] = {}
        # A prompt was sent or read and its turn has said nothing back yet.
        self._turn_open = False
        self.standing: dict = EMPTY_STANDING
        self._turns = 0  # results seen
        self._report_turn = -1  # self._turns when the current report was made
        self.last_index = -1  # the "i" of the last record applied
        # The "i" of the last record that changed what a recap reads: a stop,
        # an exit or a recap after it leaves the point a recap covers alone.
        self.content_index = -1
        self._recaps: list[str] = []  # recap entry ids no send has folded yet
        self.last_recap_upto: int | None = None

    @property
    def rev(self) -> int:
        """The store index of the last record folded; -1 before the first."""
        return self._rev

    def entries(self) -> list[dict]:
        return list(self._entries.values())

    def entry(self, id: str) -> dict | None:
        return self._entries.get(id)

    def snapshot(self, since: int | None = None) -> dict:
        """What a subscriber gets: every entry, or, given the ``rev`` it holds,
        the entries changed after it and the ids removed after it. Live
        entries are in every delta: deltas grow them without a store record,
        so their ``rev`` does not move. A ``since`` this fold never reached
        gets everything."""
        entries = self.entries()
        if since is None or not -1 <= since <= self._rev:
            return {"rev": self._rev, "entries": [wire(e) for e in entries]}
        return {
            "rev": self._rev,
            "since": since,
            "removed": [i for i, r in self._removed.items() if r > since],
            "entries": [
                wire(e) for e in entries if e["rev"] > since or e["id"] in self._live
            ],
        }

    def apply(self, record: dict, events: list[Event] | None = None) -> list[dict]:
        """Fold one stored record; return the patch ops it caused.

        ``events`` lets a caller that already parsed the line skip a second
        parse; a re-fold passes nothing and parses the stored line.
        """
        i, ts = record["i"], record.get("ts")
        self.last_index = i
        self._rev = i
        src = record.get("src")
        if src in PARSERS:
            evs = (
                events
                if events is not None
                else self.parse(src, record.get("line", ""))
            )
            ops: list[dict] = []
            for k, ev in enumerate(evs):
                ops += self._event(f"e{i}.{k}", ts, ev)
        else:
            ops = self._own(i, ts, record)
        if any(op.get("upsert", {}).get("kind") in CONTENT_KINDS for op in ops):
            self.content_index = i
        return ops

    def parse(self, src: str, line: str) -> list[Event]:
        """One stored or live line of harness ``src``, through this fold's parser."""
        p = self._parsers.get(src)
        if p is None:
            p = self._parsers[src] = PARSERS[src]()
        return p.feed(line)

    def live(self, events: list[Event]) -> list[dict]:
        """Deltas: grow their part's entry without a store record. The part's
        closing update replaces the text, so a reload agrees once it closes."""
        ops: list[dict] = []
        for ev in events:
            if not isinstance(ev, Delta) or not ev.key:
                continue
            e = self._entries.get(ev.key)
            if e is None:
                thinking = ev.kind == "thinking"
                e = _entry(
                    ev.key,
                    "thinking" if thinking else "prose",
                    "ok",
                    None,
                    d.THINKING_GLYPH if thinking else d.PROSE_GLYPH,
                    title="Thinking" if thinking else "",
                    md="",
                )
                self._live.add(ev.key)
            ops += self._upsert({**e, "md": (e.get("md") or "") + ev.text})
        return ops

    def _end_turn(self) -> None:
        """A turn ended without a result: each parser forgets it."""
        self._turn_open = False
        for p in self._parsers.values():
            end = getattr(p, "end_turn", None)
            if end is not None:
                end()

    def _drop_live(self) -> list[dict]:
        """Entries only deltas made, whose part never closed."""
        ops: list[dict] = []
        for key in sorted(self._live):
            ops += self._remove(key)
        self._live.clear()
        return ops

    def _exact(self, want: str) -> str | None:
        """Remove and return the pending send whose text is ``want``."""
        for p in self._pending:
            if ((self._entries.get(p) or {}).get("md") or "").strip() == want.strip():
                self._pending.remove(p)
                return p
        return None

    # -- helpers -------------------------------------------------------
    def _stand(self, **changes: Any) -> None:
        if any(self.standing.get(k) != v for k, v in changes.items()):
            self.standing = {**self.standing, **changes}

    def _end_calls(self, verdict: str) -> list[dict]:
        """A call still running when its turn or its process ends will never
        get a result."""
        ops: list[dict] = []
        for e in list(self._entries.values()):
            if e["kind"] == "tool" and e["status"] == "running":
                ops += self._upsert(
                    {**e, "status": "err", "detail": {**e["detail"], "result": verdict}}
                )
        return ops

    def _take(
        self, want: str | None, command: bool, strict: bool = False
    ) -> str | None:
        """Remove and return the pending send an answer belongs to: the oldest
        whose text is ``want`` (compared stripped), else the oldest command line
        or prompt as ``command`` says, else, unless ``strict``, the oldest."""
        texts = [
            (p, (self._entries.get(p) or {}).get("md") or "") for p in self._pending
        ]
        pick = None
        if want is not None:
            pick = next((p for p, t in texts if t.strip() == want.strip()), None)
        if pick is None:
            pick = next((p for p, t in texts if t.startswith("/") == command), None)
        if pick is None and not strict and texts:
            pick = texts[0][0]
        if pick is not None:
            self._pending.remove(pick)
        return pick

    def _lose_pending(self) -> list[dict]:
        """Prompts Claude never read before its process ended."""
        ops: list[dict] = []
        while self._pending:
            e = self._entries.get(self._pending.popleft())
            if e is not None:
                ops += self._upsert({**e, "status": "lost"})
        return ops

    def activity(self) -> str:
        """One line on what the session is doing or last did, for a fleet card."""
        for e in reversed(self._entries.values()):
            if e["kind"] == "tool":
                line = (
                    f"{e['title']} · {e['summary']}" if e["status"] == "running" else ""
                )
                if line:
                    return _cut(line)
                continue
            if e["kind"] == "file":
                return _cut(f"sent {e['title']}")
            if e["kind"] == "artifact":
                return _cut(f"showed {e['title']}")
            if e["kind"] == "user" and self._turn_open:
                return "waiting for the model"
            if e["kind"] in ("prose", "user") and e.get("md"):
                first = next(
                    (ln.strip() for ln in e["md"].splitlines() if ln.strip()), ""
                )
                return _cut(first)
        return ""

    def _upsert(self, e: dict) -> list[dict]:
        e["rev"] = self._rev
        self._entries[e["id"]] = e
        return [{"upsert": e}]

    def _remove(self, id: str) -> list[dict]:
        self._entries.pop(id, None)
        self._removed[id] = self._rev
        return [{"remove": id}]

    # -- what aegis itself did ---------------------------------------
    def _own(self, i: int, ts: float | None, rec: dict) -> list[dict]:
        kind = rec.get("kind")
        if kind == "send":
            self._turn_open = True
            self._stand(report=None, turn_error="")
            # The person is back and writing: the recap has done its job.
            folded: list[dict] = []
            for rid in self._recaps:
                r = self._entries[rid]
                folded += self._upsert({**r, "detail": {**r["detail"], "folded": True}})
            self._recaps = []
            pid = f"pending:{i}"
            self._pending.append(pid)
            return folded + self._upsert(
                _entry(
                    pid,
                    "user",
                    "pending",
                    ts,
                    d.USER_GLYPH,
                    md=str(rec.get("text", "")),
                )
            )
        if kind == "spawn":
            agent = rec.get("agent") or rec.get("profile")
            star = "*" if rec.get("overridden") else ""
            line = f"spawned {agent}{star} · {rec.get('model')} · {rec.get('cwd')}"
            return self._upsert(
                _entry(f"e{i}", "system", "ok", ts, d.SYSTEM_GLYPH, summary=line)
            )
        if kind == "plan":
            items = list(rec.get("items") or [])
            self._stand(
                plan=items, did=_did(self.standing["plan"], items, self.standing["did"])
            )
            return []
        if kind == "turn_end":
            self._report_turn = self._turns
            self._stand(
                report={
                    "attention": rec.get("attention"),
                    "line": rec.get("line") or "",
                    "replies": list(rec.get("replies") or []),
                }
            )
            return []
        if kind == "recap":
            # A refresh replaces the recap on screen: one full box, never two.
            folded = []
            for rid in self._recaps:
                r = self._entries[rid]
                if not r["detail"]["folded"]:
                    folded += self._upsert(
                        {**r, "detail": {**r["detail"], "folded": True}}
                    )
            self._recaps.append(f"e{i}")
            self.last_recap_upto = rec.get("upto")
            return folded + self._upsert(
                _entry(
                    f"e{i}",
                    "recap",
                    "ok",
                    ts,
                    d.RECAP_GLYPH,
                    title="recap",
                    summary=str(rec.get("context") or ""),
                    detail={
                        "context": rec.get("context") or "",
                        "ask": rec.get("ask") or "",
                        "model": rec.get("model") or "",
                        "cost_usd": float(rec.get("cost_usd") or 0.0),
                        "duration_ms": int(rec.get("duration_ms") or 0),
                        "upto": rec.get("upto"),
                        "folded": False,
                    },
                )
            )
        if kind == "interrupt":
            self._interrupted = True
            return []
        if kind == "interrupt_timeout":
            line = f"the interrupt went unanswered for {rec.get('after_s', 10):g}s"
            self._stand(turn_error=line)
            return self._upsert(
                _entry(f"e{i}", "error", "err", ts, d.ERROR_GLYPH, summary=line)
            )
        if kind == "exit":
            stderr = "\n".join(rec.get("stderr_tail") or [])
            self._end_turn()
            self._stand(
                turn_error=f"{rec.get('harness') or 'claude'} exited with code {rec.get('code')}"
            )
            return (
                self._drop_live()
                + self._end_calls("no result")
                + self._lose_pending()
                + self._upsert(
                    _entry(
                        f"e{i}",
                        "error",
                        "err",
                        ts,
                        d.ERROR_GLYPH,
                        summary=f"{rec.get('harness') or 'claude'} exited with code {rec.get('code')}",
                        detail={"tail": stderr, "collapsed": False},
                    )
                )
            )
        if kind in ("stop", "server_stopped"):
            line = "stopped" if kind == "stop" else "the server stopped during a turn"
            self._end_turn()
            if kind == "server_stopped":
                self._stand(turn_error="the server stopped during a turn")
            return (
                self._drop_live()
                + self._end_calls("no result")
                + self._lose_pending()
                + self._upsert(
                    _entry(f"e{i}", "system", "ok", ts, d.SYSTEM_GLYPH, summary=line)
                )
            )
        if kind == "resume":
            return self._upsert(
                _entry(f"e{i}", "system", "ok", ts, d.SYSTEM_GLYPH, summary="resumed")
            )
        if kind == "close":
            return self._upsert(
                _entry(f"e{i}", "system", "ok", ts, d.SYSTEM_GLYPH, summary="closed")
            )
        if kind == "file":
            # One record per file_send; a record from before sets is one file.
            sent = [_sent_file(f) for f in rec.get("files") or [rec]]
            more = len(sent) - 1
            return self._upsert(
                _entry(
                    f"e{i}",
                    "file",
                    "ok",
                    ts,
                    d.FILE_GLYPH,
                    title=sent[0]["name"] + (f" +{more}" if more else ""),
                    summary=f"{len(sent)} files" if more else sent[0]["summary"],
                    md=rec.get("caption"),
                    detail={"files": sent},
                )
            )
        if kind == "artifact":
            aid = str(rec.get("artifact_id"))
            old = self._entries.get(aid)
            det = (
                dict(old["detail"])
                if old
                else {
                    "state": rec.get("state"),
                    "state_by": "agent",
                    # The record that last set the state: a browser pushes the
                    # agent's state into a live frame only when this moved, so
                    # a page's own emit never echoes the state back to it.
                    "state_rev": i,
                    "events": [],
                    "submitted": None,
                    "label": None,
                    "ended_ts": None,
                }
            )
            det.update(
                artifact_id=aid,
                file_id=rec.get("file_id"),
                url=files.url(str(rec.get("file_id")), str(rec.get("name"))),
                started=rec.get("started"),
            )
            status = old["status"] if old else "live"
            return self._upsert(
                _entry(
                    aid,
                    "artifact",
                    status,
                    old["ts"] if old else ts,
                    d.ARTIFACT_GLYPH,
                    title=str(rec.get("title") or ""),
                    summary=status,
                    md=rec.get("caption"),
                    detail=det,
                )
            )
        if kind in (
            "artifact_state",
            "artifact_event",
            "artifact_submit",
            "artifact_close",
        ):
            e = self._entries.get(str(rec.get("artifact_id")))
            if e is None:
                return []  # a record for a page that never landed here
            det = dict(e["detail"])
            status = e["status"]
            if kind == "artifact_state":
                det["state"], det["state_by"], det["state_rev"] = (
                    rec.get("state"),
                    rec.get("by") or "page",
                    i,
                )
            elif kind == "artifact_event":
                det["events"] = (
                    det["events"]
                    + [{"name": rec.get("name"), "data": rec.get("data"), "ts": ts}]
                )[-EVENTS_KEPT:]
            elif kind == "artifact_submit":
                status = "submitted"
                det.update(
                    submitted=rec.get("data"), label=rec.get("label"), ended_ts=ts
                )
            else:
                status = "closed"
                det.update(label=rec.get("label"), ended_ts=ts)
            return self._upsert(
                {**e, "status": status, "summary": status, "detail": det}
            )
        if kind == "peek":
            # A file tool's file, copied because the person asked to see it:
            # it opens inside that tool's row, the latest copy replacing any.
            row = self._entries.get(str(rec.get("entry")))
            if row is None or row["kind"] != "tool":
                return []
            sent = [_sent_file(f) for f in rec.get("files") or []]
            return self._upsert(
                {
                    **row,
                    "detail": {
                        **row["detail"],
                        "peek": {"ts": ts, "glyph": d.FILE_GLYPH, "files": sent},
                    },
                }
            )
        if kind == "configure":
            parts = [
                f"{k} → {rec[k]}"
                for k in ("model", "effort", "permission")
                if rec.get(k)
            ]
            when = {
                "next_turn": "from the next turn",
                "on_resume": "when it resumes",
            }.get(str(rec.get("when") or ""))
            line = " · ".join(parts) + (f" ({when})" if when else "")
            return self._upsert(
                _entry(f"e{i}", "system", "ok", ts, d.SYSTEM_GLYPH, summary=line)
            )
        if kind == "harness_error":
            ops: list[dict] = []
            line = rec.get("line")
            if line:
                pid = self._exact(str(line))
                if pid is not None:
                    ops += self._upsert({**self._entries[pid], "status": "lost"})
            return ops + self._upsert(
                _entry(
                    f"e{i}",
                    "error",
                    "err",
                    ts,
                    d.ERROR_GLYPH,
                    summary=str(rec.get("text")),
                )
            )
        if kind == "reset":
            self._end_turn()
            return self._upsert(
                _entry(
                    f"e{i}",
                    "system",
                    "ok",
                    ts,
                    d.SYSTEM_GLYPH,
                    summary=str(rec.get("text")),
                )
            )
        if kind == "damaged":
            n = rec.get("count", 0)
            return self._upsert(
                _entry(
                    f"e{i}",
                    "system",
                    "ok",
                    ts,
                    d.SYSTEM_GLYPH,
                    summary=f"skipped {n} damaged lines",
                )
            )
        return []

    # -- what Claude said ----------------------------------------------
    def _event(self, id: str, ts: float | None, ev: Event) -> list[dict]:
        if isinstance(ev, Echo):
            ops: list[dict] = []
            typed = None
            if ev.expands:
                pid = self._exact(ev.text)
                if pid is None:
                    pid = self._take(None, command=True, strict=True)
                    if pid is not None:
                        typed = (self._entries.get(pid) or {}).get("md")
                if pid is None:
                    pid = self._take(None, command=False)
            else:
                pid = self._take(ev.text, command=False)
            if pid is not None:
                ops += self._remove(pid)
            self._turn_open = True
            if ev.text.startswith("> from "):
                # An inbox message: a monitor wake, a queue result, a handoff.
                header = ev.text.splitlines()[0].removeprefix("> from ").strip()
                return ops + self._upsert(
                    _entry(
                        id, "inbox", "ok", ts, d.COMMS_GLYPH, title=header, md=ev.text
                    )
                )
            if typed is not None:
                return ops + self._upsert(
                    _entry(
                        id,
                        "user",
                        "ok",
                        ts,
                        d.USER_GLYPH,
                        md=typed,
                        detail={"tail": ev.text},
                    )
                )
            return ops + self._upsert(
                _entry(id, "user", "ok", ts, d.USER_GLYPH, md=ev.text)
            )

        if isinstance(ev, LocalCommand):
            line = f"/{ev.command} {ev.args}".strip()
            pid = self._take(line, command=True, strict=True)
            ops = self._remove(pid) if pid is not None else []
            return ops + self._upsert(
                _entry(
                    id,
                    "command",
                    "ok",
                    ts,
                    d.COMMAND_GLYPH,
                    title=line,
                    md=ev.text or None,
                )
            )

        if isinstance(ev, CommandEcho):
            line = f"/{ev.name} {ev.args}".strip()
            pid = self._take(line, command=True)
            ops = self._remove(pid) if pid is not None else []
            return ops + self._upsert(
                _entry(id, "user", "ok", ts, d.USER_GLYPH, md=line)
            )

        if isinstance(ev, CommandOutput):
            # The note a set_model control request leaves answers no send; its
            # configure record already shows. Taking a queued command's place
            # would title the note with that command and drop its real output.
            if ev.text.startswith("Set model to"):
                return []
            pid = self._take(None, command=True, strict=True)
            if pid is None:
                return []
            title = (self._entries[pid].get("md") or "").strip()
            return self._remove(pid) + self._upsert(
                _entry(
                    id,
                    "command",
                    "ok",
                    ts,
                    d.COMMAND_GLYPH,
                    title=title,
                    md=ev.text or None,
                )
            )

        if isinstance(ev, Reset):
            pid = self._take("/clear", command=True, strict=True)
            ops = self._remove(pid) if pid is not None else []
            return ops + self._upsert(
                _entry(
                    id,
                    "system",
                    "ok",
                    ts,
                    d.SYSTEM_GLYPH,
                    summary="context cleared; Claude started a new conversation",
                )
            )

        if isinstance(ev, (Text, Thinking, ToolCall, ToolOutput)) and ev.parent:
            return self._subagent_step(ev.parent)

        if isinstance(ev, Text):
            eid = ev.key or id
            if ev.key:
                self._live.discard(ev.key)
            if not ev.text.strip():
                return []
            ops = self._upsert(
                _entry(eid, "prose", "ok", ts, d.PROSE_GLYPH, md=ev.text)
            )
            self._stand(last_message=eid)
            return ops

        if isinstance(ev, Thinking):
            eid = ev.key or id
            if ev.key:
                self._live.discard(ev.key)
                if not ev.text.strip():
                    return []  # an opening part
            return self._upsert(
                _entry(
                    eid,
                    "thinking",
                    "ok",
                    ts,
                    d.THINKING_GLYPH,
                    title="Thinking",
                    summary="" if ev.text.strip() else "thought",
                    md=ev.text or None,
                )
            )

        if isinstance(ev, ToolCall):
            self._calls[ev.id] = ev
            detail = {"args": d.format_tool_args(ev.name, ev.input), "steps": 0}
            if path := d.file_path(ev.name, ev.input):
                detail["path"] = path  # what the row's "show file" copies (file.peek)
            return self._upsert(
                _entry(
                    ev.id,
                    "tool",
                    "running",
                    ts,
                    d.tool_glyph(ev.name),
                    title=d.tool_title(ev.name),
                    summary=d.tool_label(ev.name, ev.input),
                    detail=detail,
                )
            )

        if isinstance(ev, ToolOutput):
            call = self._calls.get(ev.id)
            name = call.name if call else "result"
            inp = call.input if call else {}
            pair = d.edit_pair(name, inp)
            prev = self._entries.get(ev.id)
            detail: dict[str, Any] = (
                dict(prev["detail"]) if prev else {"args": "", "steps": 0}
            )
            detail.update(
                result=d.result_digest(name, ev.text, ev.is_error, pair),
                tail=d.output_tail(ev.text),
            )
            if pair is not None and not ev.is_error:
                removed, added, elided = d.diff_window(pair[1], pair[2])
                detail["diff"] = {
                    "path": pair[0],
                    "removed": removed,
                    "added": added,
                    "elided": elided,
                }
            return self._upsert(
                _entry(
                    ev.id,
                    "tool",
                    "err" if ev.is_error else "ok",
                    prev["ts"] if prev else ts,
                    d.tool_glyph(name),
                    title=d.tool_title(name),
                    summary=prev["summary"] if prev else "",
                    detail=detail,
                )
            )

        if isinstance(ev, Result):
            secs = (ev.duration_ms or 0) / 1000
            parts = [f"done in {secs:.1f}s"]
            if ev.cost_usd is not None:
                # Claude's total_cost_usd is a running total for the process.
                turn = ev.cost_usd - self._last_cost
                self._last_cost = ev.cost_usd
                if turn > 0:
                    parts.append(d.money(turn))
            if ev.stop_reason and ev.stop_reason not in ("end_turn", "stop_sequence"):
                parts.append(ev.stop_reason)
            interrupted, self._interrupted = self._interrupted, False
            self._turns += 1
            report = self.standing["report"]
            if report is not None and self._report_turn < self._turns - 1:
                report = None  # made in an earlier turn; this one ended without one
            error = ""
            if ev.is_error and not interrupted:
                error = f"turn failed ({ev.subtype or 'error'})"
            self._stand(report=report, turn_error=error)
            ops = self._end_calls("interrupted" if interrupted else "no result")
            ops += self._drop_live()
            self._turn_open = False
            if ev.turns == 0 and len(parts) == 1 and not ev.is_error:
                return ops  # a local command that cost nothing: its own entry says it
            if ev.is_error:
                head = (
                    "interrupted"
                    if interrupted
                    else f"turn failed ({ev.subtype or 'error'})"
                )
                return ops + self._upsert(
                    _entry(
                        id,
                        "error",
                        "err",
                        ts,
                        d.ERROR_GLYPH,
                        summary=" · ".join([head, *parts[1:]]),
                    )
                )
            return ops + self._upsert(
                _entry(
                    id, "system", "ok", ts, d.SYSTEM_GLYPH, summary=" · ".join(parts)
                )
            )

        if isinstance(ev, Init):
            if self._seen_init:
                return []
            self._seen_init = True
            line = " · ".join(
                x
                for x in (
                    f"{ev.harness} {ev.version}" if ev.version else ev.harness,
                    ev.model,
                )
                if x
            )
            return self._upsert(
                _entry(id, "system", "ok", ts, d.SYSTEM_GLYPH, summary=line)
            )

        if isinstance(ev, Compact):
            line = (
                f"context compacted: {ev.pre_tokens // 1000}k → {ev.post_tokens // 1000}k tokens"
                if ev.pre_tokens
                else "context compacted"
            )
            return self._upsert(
                _entry(id, "system", "ok", ts, d.SYSTEM_GLYPH, summary=line)
            )

        if isinstance(ev, Garbled):
            return self._upsert(
                _entry(
                    id,
                    "system",
                    "ok",
                    ts,
                    d.SYSTEM_GLYPH,
                    summary="a line from claude that is not JSON",
                    detail={"tail": ev.raw[:200], "collapsed": True},
                )
            )
        return []

    def _subagent_step(self, parent: str) -> list[dict]:
        e = self._entries.get(parent)
        if e is None:
            return []
        e = {**e, "detail": {**e["detail"], "steps": e["detail"].get("steps", 0) + 1}}
        return self._upsert(e)


_MARKUP = re.compile(r"(\*\*|__|`|^#+\s*|^>\s*)")


def _cut(line: str, n: int = 80) -> str:
    """One line for a card: Markdown emphasis, code and heading marks dropped."""
    line = _MARKUP.sub("", line).strip()
    return line if len(line) <= n else line[: n - 1] + "…"


def fold_records(records: list[dict]) -> Fold:
    f = Fold()
    for r in records:
        f.apply(r)
    return f
