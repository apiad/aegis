"""Cursor's ACP messages, one stdout line at a time, as typed events.

The events are ``claude/stream.py``'s, so the fold keeps one set of rules for
every harness. Measured on cursor-agent 2026.10.14 (spec
``2026-10-19-aegis-2-cursor-harness-design.md``; fixture
``tests/fixtures/cursor/acp-turn.jsonl``):

- A line is an aegis ``aegis/*`` line that process.py wrote, or a
  ``session/update`` notification carrying one ``update``; anything else is
  ``Ignored`` and drawn by nothing.
- Text arrives only as chunks and never repeats itself (measured: no update
  carries the whole message, the ``session/prompt`` answer carries only a
  ``stopReason``), so each line carries the open block's text so far as the
  keyed ``Text`` / ``Thinking`` the fold upserts in place: the entry's id is
  its key, so the row stays where it opened, and the store keeps every chunk
  line. Claude's and Codex's delta lines are dropped as recoverable from
  their closing update; Cursor has no such update to recover from, so here
  the chunk lines are what a re-fold replays. Keys are ``p`` / ``t`` plus a
  count that never resets, assigned in line order, so a re-fold rebuilds the
  same blocks from the same lines. A switch of kind closes the block it
  left, and the turn's ``aegis/turn_done`` flushes the rest.
- A tool is ``tool_call`` then ``tool_call_update``: the first update fills
  ``rawInput`` (a file's ``path``), a diff completion carries
  ``{path, oldText, newText}`` as content, and a shell's result arrives in
  ``rawOutput`` ``{exitCode, stdout, stderr}`` (measured in cursor-agent's
  own ``presentCompletedToolCall``, which puts a write's diff in ``content``
  and everything else's result in ``rawOutput``). The statuses are ``pending``,
  ``in_progress``, ``completed`` and ``failed``. An update that carries a
  better input or title re-emits the call, so the row ends with what the tool
  really did; the fold upserts it by id.
- ``session_info_update`` carries the title. ``available_commands_update``
  carries the command list, which process.py keeps for the catalog instead of
  an event. There is no usage line of any kind: Cursor reports no tokens over
  ACP, so no ``Usage`` or ``Step`` ever exists, and a card for this harness
  shows neither cost nor context.
"""

from __future__ import annotations

import json
from typing import Any

from ..claude.stream import (
    Echo,
    Event,
    Garbled,
    Ignored,
    Init,
    Result,
    Text,
    Thinking,
    Title,
    ToolCall,
    ToolOutput,
)
from .config import SERVER, tool_name

LABEL = "Cursor Agent"
OWN = "aegis/"


def _dict(v: Any) -> dict:
    return v if isinstance(v, dict) else {}


def _block_text(block: Any) -> str:
    """A content block's text: only text has any (an image or a resource
    does not), and Cursor wraps its own in ``{type: "content", content: ...}``
    or gives it flat as ``{type: "text", text}``."""
    b = _dict(block)
    if b.get("type") == "text":
        return str(b.get("text") or "")
    inner = b.get("content")
    if isinstance(inner, dict):
        return str(inner.get("text") or "")
    return ""


def _output(content: list, raw: dict | None = None) -> tuple[str, dict | None]:
    """A tool call's completion as ``(text, diff)``: every text block joined,
    the one ``{type: "diff", path, oldText, newText}`` if there is one, and
    what ``rawOutput`` carries. Cursor's own ``presentCompletedToolCall``
    (measured in its bundle) sends ``content`` only for a file write
    (the diff) and puts a shell's result in ``rawOutput``
    ``{exitCode, stdout, stderr}``; a read answers ``{content}`` there.
    """
    texts: list[str] = []
    diff: dict | None = None
    for c in content:
        if not isinstance(c, dict):
            continue
        if c.get("type") == "diff":
            diff = c
        else:
            text = _block_text(c)
            if text:
                texts.append(text)
    if isinstance(raw, dict):
        for key in ("stdout", "stderr"):
            text = str(raw.get(key) or "")
            if text:
                texts.append(text)
        code = raw.get("exitCode")
        if isinstance(code, int) and code:
            texts.append(f"exit code {code}")
        inner = raw.get("content")
        blocks = inner if isinstance(inner, list) else [inner]
        for c in blocks:
            if isinstance(c, dict):
                text = _block_text(c)
                if text:
                    texts.append(text)
    return "\n".join(texts), diff


class Parser:
    def __init__(self) -> None:
        self.session_id: str | None = None
        self.model: str | None = None
        self.cwd: str | None = None
        self.title: str | None = None
        # The open block: its key, its kind ("prose" | "thinking") and what
        # its chunks have said so far.
        self.key: str | None = None
        self.kind: str | None = None
        self.acc: str = ""
        self.blocks = 0
        # What is known about each open tool call, to re-emit it as its input
        # and title arrive (tool id -> (name, input)).
        self.tools: dict[str, list] = {}

    def end_turn(self) -> None:
        """The turn ended: nothing of it may reach the next."""
        self.key = None
        self.kind = None
        self.acc = ""

    def feed(self, line: str) -> list[Event]:
        try:
            obj: Any = json.loads(line)
        except ValueError:
            return [Garbled(raw=line)]
        if not isinstance(obj, dict):
            return [Garbled(raw=line)]
        method = str(obj.get("method") or "")
        params = _dict(obj.get("params"))
        if method.startswith(OWN):
            handler = getattr(self, "_" + method.partition("/")[2], None)
            return handler(params) if handler else [Ignored(type=method)]
        if method != "session/update":
            return [Ignored(type=method)]
        update = _dict(params.get("update"))
        kind = str(update.get("sessionUpdate") or "")
        handler = getattr(self, "_u_" + kind, None)
        return handler(update) if handler else [Ignored(type=kind)]

    # -- aegis's own lines ---------------------------------------------
    def _session(self, p: dict) -> list[Event]:
        self.session_id = _str(p.get("session_id"))
        self.model = _str(p.get("model"))
        self.cwd = _str(p.get("cwd"))
        return [
            Init(
                session_id=self.session_id,
                model=self.model,
                version=None,
                harness=LABEL,
                cwd=self.cwd,
            )
        ]

    def _turn(self, p: dict) -> list[Event]:
        """Model or permission changed mid-session (process.set): the model
        line of the transcript follows it, as Codex's ``aegis/turn`` does."""
        model = _str(p.get("model"))
        if not model or model == self.model:
            return []
        self.model = model
        return [
            Init(
                session_id=self.session_id,
                model=model,
                version=None,
                harness=LABEL,
                cwd=self.cwd,
            )
        ]

    def _echo(self, p: dict) -> list[Event]:
        text = str(p.get("text") or "")
        return [Echo(text=text)] if text else []

    def _command(self, p: dict) -> list[Event]:
        line = str(p.get("line") or "")
        return [Echo(text=line)] if line else []

    def _turn_done(self, p: dict) -> list[Event]:
        """The turn's last line: flush the text the chunks drew as the whole
        blocks the store keeps, then close the turn with a Result.

        ``stop_reason`` is Cursor's own ``stopReason`` (``end_turn``,
        ``cancelled``, ``refusal``, ``max_tokens``, ``max_turn_requests``), or
        ``interrupted`` when aegis killed the child to stop it."""
        out = self._flush()
        stop = str(p.get("stop_reason") or "end_turn")
        span = p.get("duration_ms")
        # The reason travels in ``stop_reason`` only when the turn succeeded:
        # on a failed one the fold's head already says ``subtype``, and an
        # interrupted turn is recognised by its own ``interrupt`` record
        # (entries.py), so repeating it there would print it twice.
        out.append(
            Result(
                is_error=stop != "end_turn",
                subtype=(
                    "success"
                    if stop == "end_turn"
                    else "interrupted"
                    if stop in ("interrupted", "cancelled")
                    else stop
                ),
                duration_ms=span if isinstance(span, int) else None,
                cost_usd=None,
                stop_reason="end_turn" if stop == "end_turn" else None,
                context_window=None,
            )
        )
        self.end_turn()
        return out

    # -- one block of text ----------------------------------------------
    def _flush(self) -> list[Event]:
        """The open block as its whole event, if any of it was said."""
        if self.key is None or not self.acc.strip():
            self.key, self.kind, self.acc = None, None, ""
            return []
        out = [self._part()]
        self.key, self.kind, self.acc = None, None, ""
        return out

    def _part(self) -> Event:
        """The open block as the keyed event carrying its text so far."""
        if self.kind == "thinking":
            return Thinking(text=self.acc, parent=None, usage=None, key=self.key)
        return Text(text=self.acc, parent=None, usage=None, key=self.key)

    def _chunk(self, p: dict, kind: str) -> list[Event]:
        """One more piece of an open text: the block's whole text so far, as
        the keyed part event the fold upserts in place (module docstring:
        Cursor sends no closing update, so this line is the text's record).

        The block the previous kind had is closed first, so its line holds
        all of it, and the new block starts its own key on this line.
        """
        text = ""
        content = p.get("content")
        if isinstance(content, dict):
            text = str(content.get("text") or "")
        elif isinstance(content, list):
            text = "".join(_block_text(c) for c in content)
        if not text:
            return []  # a chunk with nothing in it opens no block
        out: list[Event] = []
        if self.kind != kind or self.key is None:
            out += self._flush()
            self.blocks += 1
            self.key = f"{'t' if kind == 'thinking' else 'p'}{self.blocks}"
            self.kind = kind
        self.acc += text
        return out + [self._part()]

    # -- session/update --------------------------------------------------
    def _u_session_info_update(self, p: dict) -> list[Event]:
        title = _str(p.get("title"))
        if not title or title == self.title:
            return []
        self.title = title
        return [Title(text=title)]

    def _u_agent_thought_chunk(self, p: dict) -> list[Event]:
        return self._chunk(p, "thinking")

    def _u_agent_message_chunk(self, p: dict) -> list[Event]:
        return self._chunk(p, "prose")

    def _u_tool_call(self, p: dict) -> list[Event]:
        tid = _str(p.get("toolCallId"))
        if not tid:
            return []
        name, inp = tool_name(
            str(p.get("kind") or ""),
            str(p.get("title") or ""),
            p.get("rawInput") if isinstance(p.get("rawInput"), dict) else {},
        )
        self.tools[tid] = [name, inp]
        return [ToolCall(id=tid, name=name, input=inp, parent=None, usage=None)]

    def _u_tool_call_update(self, p: dict) -> list[Event]:
        tid = _str(p.get("toolCallId"))
        if not tid:
            return []
        raw = p.get("rawInput") if isinstance(p.get("rawInput"), dict) else None
        raw_out = p.get("rawOutput") if isinstance(p.get("rawOutput"), dict) else None
        title = str(p.get("title") or "")
        content = p.get("content") if isinstance(p.get("content"), list) else []
        status = str(p.get("status") or "")
        text, diff = _output(content, raw_out)
        state = self.tools.get(tid)
        out: list[Event] = []
        if state is None:
            state = list(tool_name(str(p.get("kind") or ""), title, raw or {}))
            self.tools[tid] = state
        name, inp = state
        changed = False
        if title.startswith(f"{SERVER}: ") and not name.startswith("mcp__"):
            state[0] = name = f"mcp__{SERVER}__{title.partition(': ')[2]}"
            changed = True
        if raw:
            _, inp = tool_name(str(p.get("kind") or ""), title, raw)
            state[1] = inp
            changed = True
        if diff is not None and isinstance(diff.get("path"), str):
            old = diff.get("oldText")
            state[1] = inp = {
                **inp,
                "file_path": diff["path"],
                "old_string": old if isinstance(old, str) else "",
                "new_string": str(diff.get("newText") or ""),
            }
            changed = True
        if changed:
            out.append(ToolCall(id=tid, name=name, input=inp, parent=None, usage=None))
        if status in ("completed", "failed"):
            out.append(
                ToolOutput(
                    id=tid,
                    text=text,
                    is_error=status == "failed",
                    parent=None,
                )
            )
            self.tools.pop(tid, None)
        return out

    def _u_available_commands_update(self, p: dict) -> list[Event]:
        # The list itself is process.py's (the catalog), never an entry.
        return []


def _str(v: Any) -> str | None:
    return v if isinstance(v, str) and v else None
