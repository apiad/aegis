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
from . import describe as d


class _Stateless:
    """Claude's stream-json needs no memory between lines."""

    def feed(self, line: str) -> list[Event]:
        return parse(line)


# The store's src tag -> a parser factory. A fold keeps one parser per tag, so
# a harness whose events need earlier lines (OpenCode's) sees them in order.
PARSERS: dict[str, Any] = {"claude": _Stateless}


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

    def entries(self) -> list[dict]:
        return list(self._entries.values())

    def apply(self, record: dict, events: list[Event] | None = None) -> list[dict]:
        """Fold one stored record; return the patch ops it caused.

        ``events`` lets a caller that already parsed the line skip a second
        parse; a re-fold passes nothing and parses the stored line.
        """
        i, ts = record["i"], record.get("ts")
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
            return ops
        return self._own(i, ts, record)

    def parse(self, src: str, line: str) -> list[Event]:
        """One stored or live line of harness ``src``, through this fold's parser."""
        p = self._parsers.get(src)
        if p is None:
            p = self._parsers[src] = PARSERS[src]()
        return p.feed(line)

    # -- helpers -------------------------------------------------------
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
            if e["kind"] in ("prose", "user") and e.get("md"):
                first = next(
                    (ln.strip() for ln in e["md"].splitlines() if ln.strip()), ""
                )
                return _cut(first)
        return ""

    def _upsert(self, e: dict) -> list[dict]:
        self._entries[e["id"]] = e
        return [{"upsert": e}]

    def _remove(self, id: str) -> list[dict]:
        self._entries.pop(id, None)
        return [{"remove": id}]

    # -- what aegis itself did ---------------------------------------
    def _own(self, i: int, ts: float | None, rec: dict) -> list[dict]:
        kind = rec.get("kind")
        if kind == "send":
            pid = f"pending:{i}"
            self._pending.append(pid)
            return self._upsert(
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
        if kind == "interrupt":
            self._interrupted = True
            return []
        if kind == "interrupt_timeout":
            return self._upsert(
                _entry(
                    f"e{i}",
                    "error",
                    "err",
                    ts,
                    d.ERROR_GLYPH,
                    summary=f"the interrupt went unanswered for {rec.get('after_s', 10):g}s",
                )
            )
        if kind == "exit":
            stderr = "\n".join(rec.get("stderr_tail") or [])
            return (
                self._end_calls("no result")
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
            return (
                self._end_calls("no result")
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
            url = files.url(str(rec.get("file_id")), str(rec.get("name")))
            return self._upsert(
                _entry(
                    f"e{i}",
                    "file",
                    "ok",
                    ts,
                    d.FILE_GLYPH,
                    title=str(rec.get("name")),
                    summary=f"{files.human_size(int(rec.get('size') or 0))} · {rec.get('mime')}",
                    md=rec.get("caption"),
                    detail={
                        "file_id": rec.get("file_id"),
                        "url": url,
                        "download": f"{url}?download=1",
                        "preview": rec.get("preview"),
                        "mime": rec.get("mime"),
                        "size": rec.get("size"),
                        "excerpt": rec.get("excerpt"),
                    },
                )
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
            pid = self._take(ev.text, command=False)
            if pid is not None:
                ops += self._remove(pid)
            if ev.text.startswith("> from "):
                # An inbox message: a monitor wake, a queue result, a handoff.
                header = ev.text.splitlines()[0].removeprefix("> from ").strip()
                return ops + self._upsert(
                    _entry(
                        id, "inbox", "ok", ts, d.COMMS_GLYPH, title=header, md=ev.text
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
            if not ev.text.strip():
                return []
            return self._upsert(
                _entry(id, "prose", "ok", ts, d.PROSE_GLYPH, md=ev.text)
            )

        if isinstance(ev, Thinking):
            return self._upsert(
                _entry(
                    id,
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
            return self._upsert(
                _entry(
                    ev.id,
                    "tool",
                    "running",
                    ts,
                    d.tool_glyph(ev.name),
                    title=d.tool_title(ev.name),
                    summary=d.tool_label(ev.name, ev.input),
                    detail={"args": d.format_tool_args(ev.name, ev.input), "steps": 0},
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
                collapsed=not ev.is_error,
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
            ops = self._end_calls("interrupted" if interrupted else "no result")
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
            line = f"context compacted: {ev.pre_tokens // 1000}k → {ev.post_tokens // 1000}k tokens"
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
