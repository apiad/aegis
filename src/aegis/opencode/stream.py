"""OpenCode's event stream, one ``data:`` payload at a time, as typed events.

The events are the ones ``claude/stream.py`` defines, so the fold keeps one set
of rules for both harnesses. Measured on OpenCode 1.18.31 (spec
``2026-10-08-aegis-2-opencode-harness-design.md``; fixtures in
``tests/fixtures/opencode/``, recorded by ``scripts/record_opencode.py``):

- A message's role arrives in ``message.updated`` before its parts.
- A text or reasoning part opens with an empty ``message.part.updated``,
  streams as ``message.part.delta`` and closes with its full text; the part id
  is the entry id, so all three touch one entry.
- A tool part goes ``pending`` (no input), ``running`` (input), then
  ``completed`` or ``error``, under one ``callID``.
- ``session.idle`` ends a turn. An abort sends ``session.error`` first, then
  ``session.idle``, then completes the aborted tool part, then idles again: a
  call still open at the idle is closed, and its late part changes nothing.
- A ``task`` call runs a child session, whose events are steps of that call.

The parser keeps what one line cannot carry alone (roles, part kinds, costs,
the turn's span), so it must see every stored line in order, which is why the
fold owns it. Every value comes from the lines and none from the clock, so a
fold of the store equals the live fold.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..claude.stream import (
    Compact,
    Delta,
    Echo,
    Event,
    Garbled,
    Ignored,
    Init,
    Result,
    Step,
    Text,
    Thinking,
    Title,
    ToolCall,
    ToolOutput,
    Usage,
)

LABEL = "OpenCode"
DELTA = "message.part.delta"
# What the store keeps; everything else on the stream (heartbeats, plugins,
# diffs, deltas) carries nothing a reload needs.
STORED = frozenset(
    {
        "session.created",
        "session.updated",
        "session.status",
        "session.idle",
        "session.error",
        "session.compacted",
        "message.updated",
        "message.part.updated",
    }
)
PLACEHOLDER_TITLE = "New session - "
TOOL_NAMES = {
    "bash": "Bash",
    "read": "Read",
    "write": "Write",
    "edit": "Edit",
    "multiedit": "MultiEdit",
    "glob": "Glob",
    "grep": "Grep",
    "list": "LS",
    "webfetch": "WebFetch",
    "websearch": "WebSearch",
    "todowrite": "TodoWrite",
    "task": "Task",
}
AEGIS_TOOL = "aegis_"
_CAMEL = re.compile(r"(?<!^)(?=[A-Z])")


def tool_name(name: str) -> str:
    """OpenCode's tool name in the vocabulary ``describe.py`` reads."""
    if name in TOOL_NAMES:
        return TOOL_NAMES[name]
    if name.startswith(AEGIS_TOOL):
        return "mcp__aegis__" + name[len(AEGIS_TOOL) :]
    return name


def tool_input(inp: Any) -> dict:
    """camelCase keys as snake_case: ``filePath`` is ``file_path``."""
    if not isinstance(inp, dict):
        return {}
    return {_CAMEL.sub("_", k).lower(): v for k, v in inp.items()}


def session_of(props: dict) -> str | None:
    """The session an event belongs to."""
    sid = props.get("sessionID")
    if isinstance(sid, str):
        return sid
    for key in ("info", "part"):
        d = props.get(key)
        if isinstance(d, dict):
            v = d.get("sessionID") or (d.get("id") if key == "info" else None)
            if isinstance(v, str):
                return v
    return None


def _usage(tokens: Any) -> Usage | None:
    if not isinstance(tokens, dict):
        return None
    cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
    return Usage(
        input=int(tokens.get("input") or 0),
        cache_creation=int(cache.get("write") or 0),
        cache_read=int(cache.get("read") or 0),
        output=int(tokens.get("output") or 0) + int(tokens.get("reasoning") or 0),
    )


def _dict(v: Any) -> dict:
    return v if isinstance(v, dict) else {}


def _str(v: Any) -> str | None:
    return v if isinstance(v, str) else None


class Parser:
    def __init__(self) -> None:
        self.root: str | None = None
        self.children: dict[str, str | None] = {}  # child session -> its Task call
        self.roles: dict[str, str] = {}
        self.part_kinds: dict[str, str] = {}
        self.echoed: set[str] = set()
        self.open_calls: set[str] = set()
        self.closed_calls: set[str] = set()
        self.task_call: str | None = None
        self.costs: dict[str, float] = {}
        self.turn_messages: set[str] = set()
        self.closed_messages: set[str] = set()
        self.model: str | None = None
        self.title: str | None = None
        self.turn_open = False
        self.turn_start: int | None = None
        self.turn_end: int | None = None
        self.error: str | None = None

    def end_turn(self) -> None:
        """The turn ended (an idle, or for the fold an exit, a stop, a server
        restart or a new conversation): nothing of it may reach the next."""
        self.closed_calls |= self.open_calls
        self.open_calls.clear()
        self.closed_messages |= self.turn_messages
        self.turn_messages.clear()
        self.turn_open = False
        self.turn_start = self.turn_end = None
        self.error = None

    def feed(self, line: str) -> list[Event]:
        try:
            obj: Any = json.loads(line)
        except ValueError:
            return [Garbled(raw=line)]
        if not isinstance(obj, dict):
            return [Garbled(raw=line)]
        kind = str(obj.get("type"))
        handler = getattr(self, "_" + kind.replace(".", "_").replace("-", "_"), None)
        if handler is None:
            return [Ignored(type=kind)]
        return handler(_dict(obj.get("properties")))

    # -- whose event ---------------------------------------------------
    def _belongs(self, sid: str | None) -> tuple[bool, str | None]:
        """Whether the event is this conversation's, and the Task call it is
        a step of (None for the conversation itself)."""
        if sid is None:
            return False, None
        if self.root is None and sid not in self.children:
            self.root = sid
        if sid == self.root:
            return True, None
        if sid in self.children:
            # A child is never the conversation itself, even one whose Task
            # call was not seen: its own id then names no entry.
            return True, self.children[sid] or sid
        return False, None

    # -- sessions ------------------------------------------------------
    def _session_created(self, p: dict) -> list[Event]:
        info = _dict(p.get("info"))
        sid, parent = _str(info.get("id")), _str(info.get("parentID"))
        if sid is None:
            return []
        if parent is not None:
            if parent == self.root or parent in self.children:
                self.children[sid] = self.task_call
            return []
        self.root, self.model = sid, None  # a new conversation
        return self._info(info)

    def _session_updated(self, p: dict) -> list[Event]:
        info = _dict(p.get("info"))
        if info.get("parentID"):
            return []
        ok, parent = self._belongs(_str(info.get("id")))
        return self._info(info) if ok and parent is None else []

    def _info(self, info: dict) -> list[Event]:
        out: list[Event] = []
        m = _dict(info.get("model"))
        model = (
            f"{m['providerID']}/{m['id']}"
            if m.get("providerID") and m.get("id")
            else None
        )
        if model is not None and model != self.model:
            self.model = model
            out.append(
                Init(
                    session_id=_str(info.get("id")),
                    model=model,
                    version=_str(info.get("version")),
                    harness=LABEL,
                )
            )
        title = _str(info.get("title"))
        if title and not title.startswith(PLACEHOLDER_TITLE) and title != self.title:
            self.title = title
            out.append(Title(text=title))
        return out

    def _session_status(self, p: dict) -> list[Event]:
        ok, parent = self._belongs(_str(p.get("sessionID")))
        if ok and parent is None and _dict(p.get("status")).get("type") == "busy":
            self.turn_open = True
        return []

    def _session_error(self, p: dict) -> list[Event]:
        ok, parent = self._belongs(_str(p.get("sessionID")))
        if ok and parent is None:
            self.error = str(_dict(p.get("error")).get("name") or "error")
        return []

    def _session_compacted(self, p: dict) -> list[Event]:
        ok, parent = self._belongs(_str(p.get("sessionID")))
        return [Compact(pre_tokens=0, post_tokens=0)] if ok and parent is None else []

    def _session_idle(self, p: dict) -> list[Event]:
        ok, parent = self._belongs(_str(p.get("sessionID")))
        if not ok or parent is not None or not self.turn_open:
            return []
        span = (
            self.turn_end - self.turn_start
            if self.turn_start is not None and self.turn_end is not None
            else None
        )
        result = Result(
            is_error=self.error is not None,
            subtype=self.error or "success",
            duration_ms=span,
            cost_usd=sum(self.costs.values()) if self.costs else None,
            stop_reason=None,
            context_window=None,
        )
        self.end_turn()
        return [result]

    # -- messages and parts ------------------------------------------
    def _message_updated(self, p: dict) -> list[Event]:
        info = _dict(p.get("info"))
        ok, parent = self._belongs(_str(info.get("sessionID")))
        mid, role = _str(info.get("id")), info.get("role")
        if not ok or mid is None or role not in ("user", "assistant"):
            return []
        self.roles[mid] = str(role)
        if role != "assistant":
            return []
        cost = info.get("cost")
        if isinstance(cost, (int, float)):
            self.costs[mid] = float(cost)
        if parent is not None or mid in self.closed_messages:
            return []  # a child's, or an abort's late update to a closed turn
        self.turn_messages.add(mid)
        self.turn_open = True
        t = _dict(info.get("time"))
        if isinstance(t.get("created"), int) and self.turn_start is None:
            self.turn_start = t["created"]
        if isinstance(t.get("completed"), int):
            self.turn_end = max(self.turn_end or 0, t["completed"])
        err = _dict(info.get("error"))
        if err.get("name"):
            self.error = str(err["name"])
        return []

    def _message_part_updated(self, p: dict) -> list[Event]:
        part = _dict(p.get("part"))
        ok, parent = self._belongs(_str(part.get("sessionID")))
        if not ok:
            return []
        ptype, pid = part.get("type"), str(part.get("id") or "")
        mid = str(part.get("messageID"))
        if ptype in ("text", "reasoning") and mid in self.closed_messages:
            return []  # an abort's late close of a part: its turn has ended
        role = self.roles.get(mid)
        if ptype == "text":
            text = str(part.get("text") or "")
            if role == "user":
                if (
                    parent is not None
                    or part.get("synthetic")
                    or part.get("ignored")
                    or pid in self.echoed
                    or not text.strip()
                ):
                    return []
                self.echoed.add(pid)
                return [Echo(text=text, expands=True)]
            self.part_kinds[pid] = "prose"
            return [Text(text=text, parent=parent, usage=None, key=pid)]
        if ptype == "reasoning":
            self.part_kinds[pid] = "thinking"
            text = str(part.get("text") or "")
            return [Thinking(text=text, parent=parent, usage=None, key=pid)]
        if ptype == "tool":
            return self._tool(part, parent)
        if ptype == "step-finish":
            u = _usage(part.get("tokens"))
            return [Step(usage=u, parent=parent)] if u is not None else []
        return []

    def _tool(self, part: dict, parent: str | None) -> list[Event]:
        call = str(part.get("callID") or part.get("id") or "")
        if call in self.closed_calls:
            return []  # its turn already ended: an abort's late part
        state = _dict(part.get("state"))
        status = state.get("status")
        name = tool_name(str(part.get("tool") or "?"))
        out: list[Event] = []
        if name == "Task" and parent is None and status == "pending":
            self.task_call = call  # its child session may start before it runs
        if status in ("running", "completed", "error") and call not in self.open_calls:
            self.open_calls.add(call)
            out.append(
                ToolCall(
                    id=call,
                    name=name,
                    input=tool_input(state.get("input")),
                    parent=parent,
                    usage=None,
                )
            )
            if name == "Task" and parent is None:
                self.task_call = call
        if status in ("completed", "error"):
            self.open_calls.discard(call)
            self.closed_calls.add(call)
            if call == self.task_call:
                self.task_call = None
            text = state.get("output") if status == "completed" else state.get("error")
            out.append(
                ToolOutput(
                    id=call,
                    text=str(text or ""),
                    is_error=status == "error",
                    parent=parent,
                )
            )
        return out

    def _message_part_delta(self, p: dict) -> list[Event]:
        """Always one Delta, so the session never stores a delta line; one with
        no key (a child's, or a part not seen opening) draws nothing."""
        ok, parent = self._belongs(_str(p.get("sessionID")))
        pid = str(p.get("partID") or "")
        kind = self.part_kinds.get(pid)
        if not ok or parent is not None or kind is None or p.get("field") != "text":
            return [Delta(key="", kind="", text="")]
        return [Delta(key=pid, kind=kind, text=str(p.get("delta") or ""))]
