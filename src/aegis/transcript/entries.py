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
import time
from collections import deque
from typing import Any

from .. import files
from .. import layout as canvas
from ..artifacts import EVENTS_KEPT
from ..dictation import without_note
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
)
from ..claude.stream import Parser as ClaudeParser
from ..codex.stream import Parser as CodexParser
from ..cursor.stream import Parser as CursorParser
from ..opencode.stream import Parser as OpenCodeParser
from . import describe as d
from .plan_clock import replan, switch
from .wire import wire


# The store's src tag -> a parser factory. A fold keeps one parser per tag, so
# a harness whose events need earlier lines sees them in order.
PARSERS: dict[str, Any] = {
    "claude": ClaudeParser,
    "opencode": OpenCodeParser,
    "codex": CodexParser,
    "cursor": CursorParser,
}


# From which fold level of the browser's view a kind folds: 1, the work between
# what was said; 2, also what arrived along the way. A message of yours or the
# agent's (user, command, prose) never folds, and neither does what the agent
# showed you (file, artifact). The browser draws each run of consecutive folded
# entries as one line (js/transcript.js).
FOLD_LEVEL = {
    "tool": 1,
    "thinking": 1,
    "system": 1,
    "inbox": 2,
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
    if kind == "user" and md:
        # A dictated message's note is for the agent; the row shows a mark.
        md, dictated = without_note(md)
        if dictated:
            detail = {**(detail or {}), "dictated": True}
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
# that turn failed. For the nudges (nudges.py): when and how the last turn
# ended, the plan clock's work at the last plan record, and which nudges were
# sent since they were last armed. Replaced as a whole when it changes, so a
# session compares identity to know whether to publish.
EMPTY_STANDING: dict = {
    "plan": [],
    "did": "",
    "report": None,
    "turn_error": "",
    "last_message": "",
    "clock": None,
    "ended": None,
    "plan_mark": None,
    "nudged": [],
    # the canvas (layout.py); None is the default, the transcript alone
    "layout": None,
}

NUDGE_HEADER = "> from aegis:nudge · "
NOTICE_HEADER = "> from aegis:notice · "  # notices.py


def split_notices(text: str) -> tuple[str, list[str]]:
    """The text a notice rode on, and the notice blocks after it, each after a
    blank line (notices.py). A text that is only a block carries none."""
    head, *rest = text.split("\n\n" + NOTICE_HEADER)
    return head, [NOTICE_HEADER + b for b in rest]


def _nudged(prev: list[str], text: str) -> list[str]:
    """Which nudges stay sent after a send of ``text``. A send carrying nudges
    adds theirs; any other send is a new idle stretch and re-arms the idle
    nudge. Only a plan record re-arms the plan nudge."""
    kinds = [
        ln.removeprefix(NUDGE_HEADER).split(" · ")[0]
        for ln in text.splitlines()
        if ln.startswith(NUDGE_HEADER)
    ]
    if not kinds:
        return [k for k in prev if k != "idle"]
    return sorted({*prev, *kinds})


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
        # What an attached send went out as, by pending id: the echo repeats
        # that text, while the pending entry shows what the person typed.
        self._said: dict[str, str] = {}
        self._calls: dict[str, ToolCall] = {}
        self._seen_init = False
        self._interrupted = False
        self._last_cost = 0.0
        self._parsers: dict[str, Any] = {}
        # file key -> the latest version's sent file, and where that file is:
        # its entry's id and its index in the entry's files.
        self.file_keys: dict[str, dict] = {}
        self._file_at: dict[str, tuple[str, int]] = {}
        # Entries of parts that opened or that only deltas made, whose part
        # has not closed yet.
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
        # The ts of the last record applied and of the one before it: a turn
        # that dies without a result worked until its last record.
        self._ts: float | None = None
        self._prev_ts: float | None = None
        # The person's own slash command opened this turn while the clock ran
        # idle: a /context does not end their wait.
        self._slash_idle = False
        self.standing: dict = EMPTY_STANDING
        self._turns = 0  # results seen
        self._report_turn = -1  # self._turns when the current report was made
        self._sent_at: float | None = None  # the ts of the last send
        self.last_index = -1  # the "i" of the last record applied
        # The "i" of the last record that changed what a recap reads: a stop,
        # an exit or a recap after it leaves the point a recap covers alone.
        self.content_index = -1
        self._recaps: list[str] = []  # recap entry ids no send has folded yet
        self.last_recap_upto: int | None = None
        self._btws: dict[str, str] = {}  # side note id -> entry id

    def _dockable_copies(self, records: list[dict]) -> list[dict]:
        """Stored copies that are not ``file_send`` records, with stable pane ids.

        Attachments and peeked files are already copied into the sent-files
        store. Attachments use the immutable copy id; peeks provide a stable key
        for the row and advance its version when opened again.
        """
        out = []
        for rec in records:
            one = _sent_file(rec)
            key = str(rec.get("pane_key") or f"f-{one['file_id']}")
            latest = self.file_keys.get(key)
            version = int(latest["version"]) + 1 if latest else 1
            one |= {"key": key, "version": version}
            self.file_keys[key] = one
            out.append(one)
        return out

    @property
    def rev(self) -> int:
        """The store index of the last record folded; -1 before the first."""
        return self._rev

    def entries(self) -> list[dict]:
        return list(self._entries.values())

    def entry(self, id: str) -> dict | None:
        return self._entries.get(id)

    def shown(self) -> list[str]:
        """The prose the agent wrote since the last send, in order: what the
        person can have read from it this turn. A subagent's text is never
        prose, so it is not here (#679)."""
        since = self._sent_at
        return [
            e["md"]
            for e in self._entries.values()
            if e["kind"] == "prose"
            and e.get("md")
            and (since is None or (e["ts"] or 0) >= since)
        ]

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
        self._prev_ts, self._ts = self._ts, ts
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

    def _end_turn(self, ts: float | None = None) -> None:
        """A turn ended without a result: each parser forgets it. The process
        is gone, so nothing is being waited on: an open turn worked until its
        last record, a gap since a result until ``ts``."""
        self._clock(self._prev_ts if self._turn_open else ts, "idle")
        self._turn_open = False
        for p in self._parsers.values():
            end = getattr(p, "end_turn", None)
            if end is not None:
                end()

    def _open(self, key: str, kind: str, ts: float | None) -> list[dict]:
        """A keyed part opening (OpenCode's, Codex's): its entry goes in now,
        empty, so the part sits where it opened, live and on a reload alike;
        deltas and the close fill it in place. A part that never closes goes
        with its turn. Seen already, it is left as it is, unless it is still
        empty: then this is the part closing with no text, and it goes."""
        if key in self._entries:
            if self._entries[key].get("md"):
                return []
            self._live.discard(key)
            return self._remove(key)
        self._live.add(key)
        thinking = kind == "thinking"
        return self._upsert(
            _entry(
                key,
                kind,
                "ok",
                ts,
                d.THINKING_GLYPH if thinking else d.PROSE_GLYPH,
                title="Thinking" if thinking else "",
                md="",
            )
        )

    def _unlive(self, key: str | None) -> list[dict]:
        """The live entry a whole block replaces, gone; nothing on a reload."""
        if key is None or key not in self._live:
            return []
        self._live.discard(key)
        return self._remove(key)

    def _drop_live(self) -> list[dict]:
        """Entries only deltas made, whose part never closed."""
        ops: list[dict] = []
        for key in sorted(self._live):
            ops += self._remove(key)
        self._live.clear()
        return ops

    def _text(self, pid: str) -> str:
        """The text the harness was sent for a pending send."""
        return self._said.get(pid) or (self._entries.get(pid) or {}).get("md") or ""

    def _exact(self, want: str) -> str | None:
        """Remove and return the pending send whose text is ``want``."""
        for p in self._pending:
            if self._text(p).strip() == want.strip():
                self._pending.remove(p)
                return p
        return None

    # -- helpers -------------------------------------------------------
    def _clock(self, ts: float | None, running: str) -> None:
        """Run ``running`` on the plan's clock from ``ts`` (plan_clock.py)."""
        if ts is None:
            return
        plan, clock = switch(self.standing["plan"], self.standing["clock"], ts, running)
        self._stand(plan=plan, clock=clock)

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
        texts = [(p, self._text(p)) for p in self._pending]
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
            pid = self._pending.popleft()
            self._said.pop(pid, None)
            e = self._entries.get(pid)
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
        # A part still streaming: its id goes when the whole block replaces it,
        # so the browser offers no link to it (a row menu's Copy link).
        if e["id"] in self._live:
            e["live"] = True
        else:
            e.pop("live", None)
        self._entries[e["id"]] = e
        return [{"upsert": e}]

    def _remove(self, id: str) -> list[dict]:
        self._entries.pop(id, None)
        self._removed[id] = self._rev
        return [{"remove": id}]

    # -- what aegis itself did ---------------------------------------
    def _settle_btws(self, why: str, fold: bool) -> list[dict]:
        """Fail every side note still running; with ``fold``, fold them all.
        One runs at a time per session, so a new question means any earlier
        one still running lost its answer (a crash, a cut store)."""
        ops: list[dict] = []
        for eid in self._btws.values():
            e = self._entries.get(eid)
            if e is None:
                continue
            status = "failed" if e["status"] == "running" else e["status"]
            detail: dict = dict(e["detail"])
            if status != e["status"]:
                detail["why"] = why
            if fold:
                detail["folded"] = True
            if status != e["status"] or detail != e["detail"]:
                ops += self._upsert({**e, "status": status, "detail": detail})
        return ops

    def _own(self, i: int, ts: float | None, rec: dict) -> list[dict]:
        kind = rec.get("kind")
        if kind == "send":
            clock = self.standing["clock"]
            self._slash_idle = (
                str(rec.get("text") or "").startswith("/")
                and clock is not None
                and clock["running"] == "idle"
            )
            self._turn_open = True
            self._sent_at = ts
            self._clock(ts, "work")
            self._stand(
                report=None,
                turn_error="",
                nudged=_nudged(self.standing["nudged"], str(rec.get("text") or "")),
            )
            # The person is back and writing: the recap has done its job.
            folded: list[dict] = []
            for rid in self._recaps:
                r = self._entries[rid]
                folded += self._upsert({**r, "detail": {**r["detail"], "folded": True}})
            self._recaps = []
            pid = f"pending:{i}"
            self._pending.append(pid)
            text = str(rec.get("text", ""))
            head, notices = split_notices(text)
            attached = self._dockable_copies(rec.get("files") or [])
            if attached or notices or without_note(head)[1]:
                self._said[pid] = text
            return folded + self._upsert(
                _entry(
                    pid,
                    "user",
                    "pending",
                    ts,
                    d.USER_GLYPH,
                    md=str(rec.get("typed") or "") if attached else head,
                    detail={"files": attached}
                    if attached
                    else {"steered": True}
                    if rec.get("steered")
                    else None,
                )
            )
        if kind == "spawn":
            agent = rec.get("agent") or rec.get("profile")
            star = "*" if rec.get("overridden") else ""
            line = f"spawned {agent}{star} · {rec.get('model')} · {rec.get('cwd')}"
            return self._upsert(
                _entry(f"e{i}", "system", "ok", ts, d.SYSTEM_GLYPH, summary=line)
            )
        if kind == "layout":
            # Written already checked (layout_ops); the fold only keeps it.
            self._stand(layout={"windows": list(rec.get("windows") or [])})
            return []
        if kind == "plan":
            items, clock = replan(
                self.standing["plan"],
                self.standing["clock"],
                list(rec.get("items") or []),
                ts if ts is not None else 0.0,
                "work" if self._turn_open else "idle",
            )
            # The work done when the agent last touched its plan: an unchanged
            # plan record leaves the clock where it was, so add what ran since.
            mark = clock["work_s"] + (
                max(0.0, (ts or 0.0) - clock["at"])
                if clock["running"] == "work"
                else 0.0
            )
            self._stand(
                plan=items,
                clock=clock,
                did=_did(self.standing["plan"], items, self.standing["did"]),
                plan_mark=round(mark, 1),
                nudged=[k for k in self.standing["nudged"] if k != "plan"],
            )
            return []
        if kind == "turn_end":
            self._report_turn = self._turns
            self._stand(
                report={
                    "attention": rec.get("attention"),
                    "line": rec.get("line") or "",
                    "replies": list(rec.get("replies") or []),
                    # When the message that opened the turn was sent: a click
                    # after it answers an ask (attention.card, answered_at).
                    "asked": self._sent_at if self._sent_at is not None else ts,
                    # Only when the agent wrote one (#354), so a report without
                    # it keeps its old shape.
                    **({"handoff": h} if (h := rec.get("handoff")) else {}),
                    # Only when asked: archive this session and the ones it
                    # started once the turn ends (registry._close_when_finished).
                    **({"close": True} if rec.get("close") else {}),
                }
            )
            return []
        if kind == "btw":
            # A side note: the newest is open, asking another folds the rest.
            ops = self._settle_btws("no answer was recorded", fold=True)
            bid = str(rec.get("id"))
            question = str(rec.get("question") or "")
            self._btws[bid] = f"e{i}"
            return ops + self._upsert(
                _entry(
                    f"e{i}",
                    "btw",
                    "running",
                    ts,
                    d.BTW_GLYPH,
                    title="btw",
                    summary=question,
                    detail={"id": bid, "question": question, "folded": False},
                )
            )
        if kind == "btw_answer":
            prev = self._entries.get(self._btws.get(str(rec.get("id")), ""))
            if prev is None:
                return []  # its question is missing: a damaged store
            status = rec.get("status")
            if status not in ("ok", "failed", "cancelled"):
                status = "failed"
            detail = {
                **prev["detail"],
                "answer": str(rec.get("answer") or ""),
                "needs_more": bool(rec.get("needs_more")),
                "why": str(rec.get("why") or ""),
                "header": str(rec.get("header") or ""),
                "model": str(rec.get("model") or ""),
                "cost_usd": float(rec.get("cost_usd") or 0.0),
                "duration_ms": int(rec.get("duration_ms") or 0),
            }
            if status == "cancelled":
                detail["folded"] = True
            return self._upsert({**prev, "status": status, "detail": detail})
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
        if kind == "exit" and rec.get("code") == 0 and rec.get("status") == "idle":
            # A clean exit between turns cut nothing: the session is asleep
            # and the next message wakes it, as a parked one (#678). A record
            # from before "status" was kept reads as an error, as it did.
            return self._upsert(
                _entry(
                    f"e{i}",
                    "system",
                    "ok",
                    ts,
                    d.SYSTEM_GLYPH,
                    summary=f"asleep: {rec.get('harness') or 'claude'} exited between turns",
                )
            )
        if kind == "exit":
            stderr = "\n".join(rec.get("stderr_tail") or [])
            self._end_turn(ts)
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
        if kind == "park":
            mins = max(1, round((rec.get("idle_s") or 0) / 60))
            return self._upsert(
                _entry(
                    f"e{i}",
                    "system",
                    "ok",
                    ts,
                    d.SYSTEM_GLYPH,
                    summary=f"asleep after {mins} min idle",
                )
            )
        if kind in ("stop", "restart", "server_stopped"):
            if kind == "stop":
                line = "asleep"
            elif kind == "restart":
                line = "restarted"
            else:
                line = rec.get("declined") or (
                    "aegis stopped unexpectedly during a turn"
                    if rec.get("crash")
                    else "the server stopped during a turn"
                )
            self._end_turn(ts)
            if kind == "server_stopped":
                self._stand(turn_error=line)
            return (
                (
                    self._settle_btws(line, fold=False)
                    if kind == "server_stopped"
                    else []
                )
                + self._drop_live()
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
        if kind in ("cascade", "cascade_exhausted", "cascade_stopped"):
            # A cascade's turn failed and fell to the next model (session.py).
            if kind == "cascade":
                until = time.strftime("%H:%M", time.localtime(rec.get("until") or ts))
                line = (
                    f"cascade: {rec.get('from')} failed ({rec.get('error')}); cools"
                    f" until {until}"
                )
                if rec.get("refused"):
                    line += f"; could not switch to {rec.get('to')}: {rec['refused']}."
                else:
                    line += f". Continuing on {rec.get('to')}."
            elif kind == "cascade_exhausted":
                line = f"cascade exhausted: {rec.get('error')}"
            else:
                by = "stopped" if rec.get("by") == "stop" else "interrupted"
                line = f"cascade: {by} before continuing on {rec.get('to')}"
            return self._upsert(
                _entry(f"e{i}", "system", "ok", ts, d.SYSTEM_GLYPH, summary=line)
            )
        if kind == "handoff_off":
            # The session a handoff started will not take over (registry.py).
            line = f"handoff called off: {rec.get('handle')} {rec.get('why')}"
            return self._upsert(
                _entry(f"e{i}", "system", "ok", ts, d.SYSTEM_GLYPH, summary=line)
            )
        if kind == "server_paused":
            # The person paused every session (app.pause_all); the stop's
            # own record follows.
            return self._upsert(
                _entry(
                    f"e{i}",
                    "system",
                    "ok",
                    ts,
                    d.SYSTEM_GLYPH,
                    summary="paused: the person paused every session",
                )
            )
        if kind == "quota_paused":
            # The quota gate interrupted the turn (app.on_quota); the
            # interrupt's own record and result follow.
            line = f"paused: quota line reached ({rec.get('why', '')})"
            return self._upsert(
                _entry(f"e{i}", "system", "ok", ts, d.SYSTEM_GLYPH, summary=line)
            )
        if kind == "close":
            self._end_turn(ts)
            return self._upsert(
                _entry(f"e{i}", "system", "ok", ts, d.SYSTEM_GLYPH, summary="closed")
            )
        if kind == "approval":
            # Something an agent made waits for a person (schedule_ops): a
            # card, never folded, that the browser fills from the object's
            # own channel, so it shows the object as it is now.
            det = {
                "object": str(rec.get("object")),
                "name": str(rec.get("name")),
                "action": str(rec.get("action")),
            }
            # On another server: the agent named it across a trusted link.
            if rec.get("server"):
                det["server"] = str(rec["server"])
            return self._upsert(
                _entry(
                    f"e{i}",
                    "approval",
                    "ok",
                    ts,
                    d.SYSTEM_GLYPH,
                    title=det["name"],
                    summary=f"{det['object']} {det['action']}",
                    detail=det,
                )
            )
        if kind == "workflow":
            # A workflow this session armed (workflows.py): one card per run,
            # never folded, that the browser draws from the workflows channel.
            run = str(rec.get("run"))
            return self._upsert(
                _entry(
                    f"wf-{run}",
                    "workflow",
                    "ok",
                    ts,
                    d.SYSTEM_GLYPH,
                    title=run,
                    summary="workflow",
                    detail={"run": run},
                )
            )
        if kind == "workflow_decision":
            # Something a run waits on a person for (workflows.py): a gate,
            # an edit or arming again. Never folded; the browser draws it from
            # the workflows channel.
            run, what = str(rec.get("run")), str(rec.get("what"))
            key = str(rec.get("key"))
            return self._upsert(
                _entry(
                    f"wfd-{run}-{what}-{key}",
                    "workflow_decision",
                    "ok",
                    ts,
                    d.SYSTEM_GLYPH,
                    title=run,
                    summary=f"workflow {what}",
                    detail={"run": run, "what": what, "key": key},
                )
            )
        if kind == "file":
            # One record per file_send; a record from before sets is one file.
            # A file sent again from one source path is a new version of one
            # file (layout.file_key), and the older copy's card says it was
            # replaced. A record from before sources keys by its id and never
            # updates. file_send refuses one path twice in a send.
            sent, ops = [], []
            for at, f in enumerate(rec.get("files") or [rec]):
                key = (
                    canvas.file_key(str(f["source"]))
                    if f.get("source")
                    else f"f-{f.get('file_id')}"
                )
                latest = self.file_keys.get(key)
                version = latest["version"] + 1 if latest else 1
                prev = self._file_at.get(key)
                old = self._entries.get(prev[0]) if prev else None
                if prev is not None and old is not None:
                    was = list(old["detail"]["files"])
                    was[prev[1]] = {**was[prev[1]], "replaced": version}
                    ops += self._upsert(
                        {**old, "detail": {**old["detail"], "files": was}}
                    )
                one = {**_sent_file(f), "key": key, "version": version}
                sent.append(one)
                self._file_at[key] = (f"e{i}", at)
                self.file_keys[key] = one
            more = len(sent) - 1
            return ops + self._upsert(
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
            if kind in ("artifact_submit", "artifact_close"):
                lay = self.standing["layout"]
                if lay and e["id"] in canvas.ids(lay):
                    # An ended page leaves the canvas; no record is written for it.
                    self._stand(layout=canvas.trim(lay, e["id"]))
            return self._upsert(
                {**e, "status": status, "summary": status, "detail": det}
            )
        if kind in ("secret", "secret_request"):
            # A secret for the person to reveal, or one asked of them
            # (secret_ops): never plaintext, only a ciphertext or a path.
            sid = str(rec.get("secret_id"))
            sent = kind == "secret"
            det = {"expires_at": rec.get("expires_at"), "by": None, "ended_ts": None}
            if sent:
                det.update(ct=rec.get("ct"), bytes=rec.get("bytes"))
            else:
                det.update(prompt=rec.get("prompt"), path=rec.get("path"))
            status = "pending" if sent else "waiting"
            return self._upsert(
                _entry(
                    sid,
                    kind,
                    status,
                    ts,
                    d.SECRET_GLYPH,
                    title=str(rec.get("caption") or "") if sent else "secret requested",
                    summary=status,
                    detail=det,
                )
            )
        if kind == "claude_login":
            # The Claude sign-in card (claude/login.py): a later record with the
            # same id adds the sessions that failed since.
            cid = str(rec.get("card_id"))
            prev = self._entries.get(cid)
            det = {
                "handles": list(rec.get("handles") or []),
                "path": rec.get("path"),
                "restarted": None,
            }
            return self._upsert(
                _entry(
                    cid,
                    "claude_login",
                    "waiting",
                    prev["ts"] if prev else ts,
                    d.ERROR_GLYPH,
                    title="Claude sign-in failed",
                    summary="waiting",
                    detail=det,
                )
            )
        if kind == "claude_login_done":
            e = self._entries.get(str(rec.get("card_id")))
            if e is None:
                return []
            det = {**e["detail"], "restarted": list(rec.get("restarted") or [])}
            return self._upsert(
                {**e, "status": "renewed", "summary": "renewed", "detail": det}
            )
        if kind in ("secret_gone", "secret_provided"):
            e = self._entries.get(str(rec.get("secret_id")))
            if e is None:
                return []
            det = {**e["detail"], "by": rec.get("by"), "ended_ts": ts}
            if kind == "secret_provided":
                status = "provided"
                det.update(
                    bytes=rec.get("bytes"), dev=rec.get("dev"), ino=rec.get("ino")
                )
            else:
                status = str(rec.get("why"))
            return self._upsert(
                {**e, "status": status, "summary": status, "detail": det}
            )
        if kind == "config_suggest":
            # A whole new .aegis.yaml an agent proposes (config_suggest.py):
            # the text and the stamp live here, so the card applies after a
            # restart.
            det = {
                "why": rec.get("why"),
                "diff": rec.get("diff"),
                "yaml": rec.get("yaml"),
                "stamp": rec.get("stamp"),
                "by": None,
            }
            return self._upsert(
                _entry(
                    str(rec.get("suggest_id")),
                    "config",
                    "pending",
                    ts,
                    d.SYSTEM_GLYPH,
                    title="config · .aegis.yaml",
                    summary="pending",
                    detail=det,
                )
            )
        if kind == "config_suggest_done":
            e = self._entries.get(str(rec.get("suggest_id")))
            if e is None:
                return []
            status = str(rec.get("outcome"))
            det = {**e["detail"], "by": rec.get("by")}
            return self._upsert(
                {**e, "status": status, "summary": status, "detail": det}
            )
        if kind == "knowledge_card":
            # A memory or skill an agent proposed, or an approved one it
            # changed or deleted (knowledge_ops.py). What the card shows and
            # the commit Undo reverts live here, so it works after a restart.
            det = {
                k: rec.get(k)
                for k in (
                    "op",
                    "item_kind",
                    "name",
                    "description",
                    "body",
                    "partial",
                    "files",
                    "type",
                    "lifetime",
                    "expires",
                    "sha",
                    "digest",
                    "items",
                )
            }
            det["by"] = None
            op = str(rec.get("op"))
            return self._upsert(
                _entry(
                    str(rec.get("card_id")),
                    "knowledge",
                    "pending",
                    ts,
                    d.KNOWLEDGE_GLYPH,
                    title=f"imported {rec.get('name')}"
                    if op == "imported"
                    else f"{rec.get('item_kind')} {rec.get('name')}",
                    summary="proposed" if op == "new" else op,
                    detail=det,
                )
            )
        if kind == "knowledge_card_item_done":
            # One item of an import card undone: the card stays, its row
            # says so (knowledge_ops.py, card_unimport).
            e = self._entries.get(str(rec.get("card_id")))
            if e is None:
                return []
            items = [
                {**x, "outcome": rec.get("outcome"), "by": rec.get("by")}
                if x.get("name") == rec.get("name")
                else x
                for x in e["detail"].get("items") or []
            ]
            return self._upsert({**e, "detail": {**e["detail"], "items": items}})
        if kind == "knowledge_card_done":
            e = self._entries.get(str(rec.get("card_id")))
            if e is None:
                return []
            status = str(rec.get("outcome"))
            det = {**e["detail"], "by": rec.get("by")}
            return self._upsert(
                {**e, "status": status, "summary": status, "detail": det}
            )
        if kind == "peek":
            # A file copied because the person asked to see it. A tool's copy
            # opens inside that row; a reply path's copy stays on the reply as
            # hidden state, so a docked pane survives a reload.
            row = self._entries.get(str(rec.get("entry")))
            if row is None or row["kind"] not in ("tool", "prose"):
                return []
            sent = self._dockable_copies(rec.get("files") or [])
            peek = {"ts": ts, "glyph": d.FILE_GLYPH, "files": sent}
            if row["kind"] == "prose":
                peeks = dict(row["detail"].get("path_peeks") or {})
                peeks[str(rec.get("index"))] = peek
                detail = {**row["detail"], "path_peeks": peeks}
            else:
                detail = {**row["detail"], "peek": peek}
            return self._upsert(
                {
                    **row,
                    "detail": detail,
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
            self._end_turn(ts)
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
        if kind == "untested":
            # A harness version outside the tested range (harness_versions.py):
            # the judgment, then where the binary is and how to install the
            # tested one, open.
            head, _, rest = str(rec.get("text") or "").partition("\n")
            return self._upsert(
                _entry(
                    f"e{i}",
                    "system",
                    "ok",
                    ts,
                    d.SYSTEM_GLYPH,
                    summary=head,
                    detail={"tail": rest, "open": True, "untested": True},
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

    def _notice_rows(self, id: str, ts: float | None, notices: list[str]) -> list[dict]:
        """Each notice a prompt or a tool result carried, as an inbox row
        under it."""
        ops: list[dict] = []
        for i, block in enumerate(notices):
            header = block.splitlines()[0].removeprefix("> from ").strip()
            ops += self._upsert(
                _entry(
                    f"{id}:notice:{i}",
                    "inbox",
                    "ok",
                    ts,
                    d.COMMS_GLYPH,
                    title=header,
                    md=block,
                )
            )
        return ops

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
            elif ev.text.startswith(NOTICE_HEADER):
                # Steered mid-turn (Session._steer_owed), with no send of its
                # own: it must not take the row of a prompt still pending.
                pid = self._exact(ev.text)
            else:
                pid = self._take(ev.text, command=False)
            carried = self._entries.get(pid) if pid is not None else None
            if pid is not None:
                self._said.pop(pid, None)
                ops += self._remove(pid)
            self._turn_open = True
            self._clock(ts, "work")
            # The notices it carried follow it as their own rows.
            head, notices = split_notices(ev.text)
            if tasks := d.task_notices(head):
                # Claude Code's own wake for a background command or a
                # subagent that ended: a row like aegis's wakes, never the
                # person's words (#699).
                for i, t in enumerate(tasks):
                    ops += self._upsert(
                        _entry(
                            id if i == 0 else f"{id}:task:{i}",
                            "inbox",
                            t["status"],
                            ts,
                            d.COMMS_GLYPH,
                            title=t["title"],
                            md=t["md"],
                            detail=t["detail"],
                        )
                    )
                return ops + self._notice_rows(id, ts, notices)
            if head.startswith("> from "):
                # An inbox message: a monitor wake, a queue result, a handoff.
                header = head.splitlines()[0].removeprefix("> from ").strip()
                # Sent into a running turn by an urgent handoff (Session.deliver).
                steered = ((carried or {}).get("detail") or {}).get("steered")
                ops += self._upsert(
                    _entry(
                        id,
                        "inbox",
                        "ok",
                        ts,
                        d.COMMS_GLYPH,
                        title=header,
                        md=head,
                        detail={"steered": True} if steered else None,
                    )
                )
                return ops + self._notice_rows(id, ts, notices)
            attached = ((carried or {}).get("detail") or {}).get("files")
            if carried is not None and attached:
                # The person's row shows what they typed and one card; the
                # path block the agent read stays in the store's text.
                ops += self._upsert(
                    _entry(
                        id,
                        "user",
                        "ok",
                        ts,
                        d.USER_GLYPH,
                        md=carried["md"],
                        detail=carried["detail"],
                    )
                )
            elif typed is not None:
                ops += self._upsert(
                    _entry(
                        id,
                        "user",
                        "ok",
                        ts,
                        d.USER_GLYPH,
                        md=typed,
                        detail={"tail": head},
                    )
                )
            else:
                ops += self._upsert(_entry(id, "user", "ok", ts, d.USER_GLYPH, md=head))
            return ops + self._notice_rows(id, ts, notices)

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
            gone = self._unlive(ev.replaces)
            if ev.key and not ev.text.strip():
                return gone + self._open(ev.key, "prose", ts)
            if ev.key:
                self._live.discard(ev.key)
            if not ev.text.strip():
                return gone
            paths = d.inline_paths(ev.text)
            ops = gone + self._upsert(
                _entry(
                    eid,
                    "prose",
                    "ok",
                    ts,
                    d.PROSE_GLYPH,
                    md=ev.text,
                    detail={"paths": paths} if paths else None,
                )
            )
            self._stand(last_message=eid)
            return ops

        if isinstance(ev, Thinking):
            eid = ev.key or id
            gone = self._unlive(ev.replaces)
            if ev.key:
                if not ev.text.strip():
                    return gone + self._open(ev.key, "thinking", ts)
                self._live.discard(ev.key)
            return gone + self._upsert(
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
            if lang := d.tool_lang(ev.name, ev.input):
                detail["lang"] = lang  # the views' language (client/js/highlight.js)
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
            # Only aegis's own results carry notices; any other tool's output
            # (a cat of a file that quotes one) is the tool's.
            text, notices = (
                split_notices(ev.text)
                if name.startswith(d.AEGIS_PREFIX)
                else (ev.text, [])
            )
            detail.update(
                result=d.result_digest(name, text, ev.is_error, pair),
                tail=d.output_tail(text),
            )
            if name == "Read" and not ev.is_error and (nl := d.numbered_lines(ev.text)):
                detail["file"] = d.file_window(*nl)
                detail.pop("tail", None)  # the file view shows the same lines
            elif name == "Write" and pair is not None and not ev.is_error:
                detail["file"] = d.file_window(1, pair[2].splitlines())
            if pair is not None and not ev.is_error and name != "Write":
                theirs = d.hunks_from_patch(ev.patch) if ev.patch else []
                hunks, elided = d.cap_hunks(theirs or d.diff_hunks(pair[1], pair[2]))
                detail["diff"] = {
                    "path": pair[0],
                    "hunks": hunks,
                    "elided": elided,
                    "numbered": "file" if theirs else "edit",
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
            ) + self._notice_rows(ev.id, ts, notices)

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
                error = (
                    "Claude sign-in failed"
                    if ev.auth_failed
                    else f"turn failed ({ev.subtype or 'error'})"
                )
            self._stand(report=report, turn_error=error)
            # A turn that handed back to the person starts idle; one that ended
            # to wait on a monitor or a queue task keeps working (spec
            # 2026-10-09-plan-timing-design.md).
            idle = report is not None or error or interrupted or self._slash_idle
            self._clock(ts, "idle" if idle else "work")
            how = (
                "interrupted"
                if interrupted
                else "error"
                if error
                else "command"
                if self._slash_idle
                else "reported"
                if report is not None
                else "silent"
            )
            self._stand(ended={"at": ts, "how": how})
            self._slash_idle = False
            ops = self._end_calls("interrupted" if interrupted else "no result")
            ops += self._drop_live()
            self._turn_open = False
            if ev.turns == 0 and len(parts) == 1 and not ev.is_error:
                return ops  # a local command that cost nothing: its own entry says it
            if ev.is_error:
                head = "interrupted" if interrupted else error
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


def tool_output(records: list[dict], tool_id: str) -> str | None:
    """The whole output of tool call ``tool_id``, parsed again from the stored
    lines; the last one wins, as in the fold. The fold keeps only its tail
    (``describe.output_tail``); a person copying the output wants all of it."""
    parsers: dict[str, Any] = {}
    found = None
    for r in records:
        src = r.get("src")
        if src not in PARSERS:
            continue
        p = parsers.get(src) or parsers.setdefault(src, PARSERS[src]())
        for ev in p.feed(r.get("line", "")):
            if isinstance(ev, ToolOutput) and ev.id == tool_id and not ev.parent:
                found = ev.text
    return found
