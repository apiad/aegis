"""Fold store records into transcript entries.

An entry is what the browser draws, with every decision already made here:
glyph, title, one-line summary, status, what collapses, the diff window. Ids
are deterministic (a tool entry is its ``tool_use_id``; anything else is
``e<record>.<block>``; a sent prompt is ``pending:<record>``), so folding the
same records twice gives the same entries, and the live session's entries
equal a fold of its store.

Two rules from Claude Code's measured behaviour live here:

- The echo creates the user entry, never the send. A prompt sent mid-turn is
  injected at the next tool boundary; the echo is when Claude read it, and
  echoes match pending prompts in send order.
- System notices make no entry.
"""

from __future__ import annotations

from collections import deque

from ..claude.stream import (
    Compact,
    Echo,
    Event,
    Garbled,
    Init,
    Result,
    Text,
    Thinking,
    ToolCall,
    ToolOutput,
    parse,
)
from . import describe as d


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

    def entries(self) -> list[dict]:
        return list(self._entries.values())

    def apply(self, record: dict, events: list[Event] | None = None) -> list[dict]:
        """Fold one stored record; return the patch ops it caused.

        ``events`` lets a caller that already parsed the line skip a second
        parse; a re-fold passes nothing and parses the stored line.
        """
        i, ts = record["i"], record.get("ts")
        if record.get("src") == "claude":
            evs = events if events is not None else parse(record.get("line", ""))
            ops: list[dict] = []
            for k, ev in enumerate(evs):
                ops += self._event(f"e{i}.{k}", ts, ev)
            return ops
        return self._own(i, ts, record)

    # -- helpers -------------------------------------------------------
    def _upsert(self, e: dict) -> list[dict]:
        self._entries[e["id"]] = e
        return [{"upsert": e}]

    def _remove(self, id: str) -> list[dict]:
        self._entries.pop(id, None)
        return [{"remove": id}]

    # -- what aegis2 itself did ---------------------------------------
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
            line = (
                f"spawned {rec.get('profile')} · {rec.get('model')} · {rec.get('cwd')}"
            )
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
            return self._upsert(
                _entry(
                    f"e{i}",
                    "error",
                    "err",
                    ts,
                    d.ERROR_GLYPH,
                    summary=f"claude exited with code {rec.get('code')}",
                    detail={"tail": stderr, "collapsed": False},
                )
            )
        if kind == "close":
            return self._upsert(
                _entry(f"e{i}", "system", "ok", ts, d.SYSTEM_GLYPH, summary="closed")
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
            if self._pending:
                ops += self._remove(self._pending.popleft())
            return ops + self._upsert(
                _entry(id, "user", "ok", ts, d.USER_GLYPH, md=ev.text)
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
                    title=ev.name,
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
            detail = dict(prev["detail"]) if prev else {"args": "", "steps": 0}
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
                    title=name,
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
            if ev.is_error:
                head = (
                    "interrupted"
                    if interrupted
                    else f"turn failed ({ev.subtype or 'error'})"
                )
                return self._upsert(
                    _entry(
                        id,
                        "error",
                        "err",
                        ts,
                        d.ERROR_GLYPH,
                        summary=" · ".join([head, *parts[1:]]),
                    )
                )
            return self._upsert(
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
                    f"Claude Code {ev.version}" if ev.version else "Claude Code",
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


def fold_records(records: list[dict]) -> Fold:
    f = Fold()
    for r in records:
        f.apply(r)
    return f
