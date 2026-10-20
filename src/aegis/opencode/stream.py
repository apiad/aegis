"""OpenCode's event stream, one ``data:`` payload at a time, as typed events.

The events are the ones ``claude/stream.py`` defines, so the fold keeps one set
of rules for every harness. Measured on OpenCode 2.0.28 (spec
``2026-10-13-opencode-2-driver.html``; fixtures in ``tests/fixtures/opencode/``,
recorded by ``scripts/record_opencode.py``). Every payload is
``{"type", "created", "data": {"sessionID", ...}}``:

- ``session.execution.started`` opens a turn and ``.succeeded``, ``.failed`` or
  ``.interrupted`` closes it, after everything else the turn emits.
- A turn is steps, each under its own ``assistantMessageID``. Text and
  reasoning open (``*.started``), stream (``*.delta``) and close (``*.ended``
  with the full text) under that id and an ``ordinal``; the three name one
  entry. ``session.step.ended`` (or ``.failed``) carries the step's tokens.
- A tool call is named in ``session.tool.input.started``, gets its input in
  ``session.tool.called`` and ends in ``session.tool.success`` or ``.failed``,
  all under one call id.
- A ``subagent`` call runs a child session, created with a ``parentID``; the
  call's ``session.tool.progress`` names the child. The child's events are
  steps of that call.
- The prompt reaches the session as ``session.inbox.enqueued``, a command as
  its expanded template.

The parser keeps what one line cannot carry alone (tool names, children, the
turn's span and cost), so it must see every stored line in order, which is why
the fold owns it. Every value comes from the lines and none from the clock, so
a fold of the store equals the live fold.
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
DELTAS = frozenset({"session.text.delta", "session.reasoning.delta"})
# What the store keeps; everything else on the stream (shells, usage ticks,
# instructions, catalog updates, deltas) carries nothing a reload needs.
STORED = frozenset(
    {
        "session.created",
        "session.renamed",
        "session.model.selected",
        "session.inbox.enqueued",
        "session.execution.started",
        "session.execution.succeeded",
        "session.execution.failed",
        "session.execution.interrupted",
        "session.text.started",
        "session.text.ended",
        "session.reasoning.started",
        "session.reasoning.ended",
        "session.tool.input.started",
        "session.tool.called",
        "session.tool.progress",
        "session.tool.success",
        "session.tool.failed",
        "session.step.ended",
        "session.step.failed",
        "session.compaction.ended",
    }
)
TURN_ENDS = frozenset(
    {
        "session.execution.succeeded",
        "session.execution.failed",
        "session.execution.interrupted",
    }
)
TOOL_NAMES = {
    "shell": "Bash",
    "read": "Read",
    "write": "Write",
    "edit": "Edit",
    "glob": "Glob",
    "grep": "Grep",
    "webfetch": "WebFetch",
    "websearch": "WebSearch",
    "subagent": "Task",
    "skill": "Skill",
}
# OpenCode's input keys, after snake-casing, in the vocabulary describe.py reads.
INPUT_KEYS = {"path": "file_path", "agent": "subagent_type"}
AEGIS_TOOL = "aegis_"
_CAMEL = re.compile(r"(?<!^)(?=[A-Z])")


def tool_name(name: str) -> str:
    """OpenCode's tool name in the vocabulary ``describe.py`` reads."""
    if name in TOOL_NAMES:
        return TOOL_NAMES[name]
    if name.startswith(AEGIS_TOOL):
        return "mcp__aegis__" + name[len(AEGIS_TOOL) :]
    return name


def tool_input(name: str, inp: Any) -> dict:
    """camelCase keys as snake_case (``oldString`` is ``old_string``), and a
    built-in tool's keys as Claude Code names them (``path`` is ``file_path``)."""
    if not isinstance(inp, dict):
        return {}
    out = {_CAMEL.sub("_", k).lower(): v for k, v in inp.items()}
    if name in TOOL_NAMES:
        out = {INPUT_KEYS.get(k, k): v for k, v in out.items()}
    return out


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


def _model(m: Any) -> str | None:
    m = _dict(m)
    if m.get("providerID") and m.get("id"):
        return f"{m['providerID']}/{m['id']}"
    return None


def _content(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    return "".join(str(c.get("text") or "") for c in content if isinstance(c, dict))


def _error(err: Any) -> str:
    err = _dict(err)
    kind, msg = _str(err.get("type")), _str(err.get("message"))
    return f"{kind}: {msg}" if kind and msg else kind or msg or "error"


def entry_key(data: dict, kind: str) -> str:
    """The entry a text or reasoning block (and its deltas) belongs to."""
    return f"{data.get('assistantMessageID')}/{kind}/{data.get('ordinal', 0)}"


class Parser:
    def __init__(self) -> None:
        self.root: str | None = None
        self.children: dict[str, str | None] = {}  # child session -> its Task call
        self.names: dict[str, str] = {}  # call -> tool name
        self.waiting: list[str] = []  # the root's subagent calls with no child yet
        self.echoed: set[str] = set()
        self.keys: set[str] = set()
        self.open_calls: set[str] = set()
        self.closed_calls: set[str] = set()
        self.cost = 0.0
        self.model: str | None = None
        self.title: str | None = None
        self.turn_start: int | None = None
        self.error: str | None = None

    def end_turn(self) -> None:
        """The turn ended (its last event, or for the fold an exit, a stop, a
        server restart or a new conversation): nothing of it may reach the
        next."""
        self.closed_calls |= self.open_calls
        self.open_calls.clear()
        self.waiting.clear()
        self.turn_start = self.error = None
        self.cost = 0.0

    def feed(self, line: str) -> list[Event]:
        try:
            obj: Any = json.loads(line)
        except ValueError:
            return [Garbled(raw=line)]
        if not isinstance(obj, dict):
            return [Garbled(raw=line)]
        kind = str(obj.get("type"))
        handler = getattr(self, "_" + kind.replace(".", "_"), None)
        if handler is None:
            return [Ignored(type=kind)]
        return handler(_dict(obj.get("data")), obj)

    # -- whose event ---------------------------------------------------
    def _belongs(self, d: dict) -> tuple[bool, str | None]:
        """Whether the event is this conversation's, and the Task call it is
        a step of (None for the conversation itself)."""
        sid = _str(d.get("sessionID"))
        if sid is None:
            return False, None
        if self.root is None and sid not in self.children:
            self.root = sid
        if sid == self.root:
            return True, None
        if sid in self.children:
            # A child is never the conversation itself, even one whose call was
            # not seen: its own id then names no entry.
            return True, self.children[sid] or sid
        return False, None

    # -- sessions ------------------------------------------------------
    def _session_created(self, d: dict, obj: dict) -> list[Event]:
        sid, parent = _str(d.get("sessionID")), _str(d.get("parentID"))
        if sid is None:
            return []
        if parent is not None:
            if parent == self.root or parent in self.children:
                self.children[sid] = self.waiting.pop(0) if self.waiting else None
            return []
        self.root, self.model = sid, None  # a new conversation
        return self._init(d, obj)

    def _init(self, d: dict, obj: dict) -> list[Event]:
        model = _model(d.get("model"))
        if model is None or model == self.model:
            return []
        self.model = model
        where = _dict(d.get("location")) or _dict(obj.get("location"))
        return [
            Init(
                session_id=self.root,
                model=model,
                version=_str(d.get("version")),
                harness=LABEL,
                cwd=_str(where.get("directory")),
            )
        ]

    def _session_model_selected(self, d: dict, obj: dict) -> list[Event]:
        ok, parent = self._belongs(d)
        return self._init(d, obj) if ok and parent is None else []

    def _session_renamed(self, d: dict, obj: dict) -> list[Event]:
        ok, parent = self._belongs(d)
        title = _str(d.get("title"))
        if not ok or parent is not None or not title or title == self.title:
            return []
        self.title = title
        return [Title(text=title)]

    def _session_compaction_ended(self, d: dict, obj: dict) -> list[Event]:
        ok, parent = self._belongs(d)
        return [Compact(pre_tokens=0, post_tokens=0)] if ok and parent is None else []

    def _session_inbox_enqueued(self, d: dict, obj: dict) -> list[Event]:
        ok, parent = self._belongs(d)
        item, iid = _dict(d.get("item")), str(d.get("inboxID") or "")
        text = str(_dict(item.get("payload")).get("text") or "")
        if not ok or parent is not None or item.get("type") != "user":
            return []
        if iid in self.echoed or not text.strip():
            return []
        self.echoed.add(iid)
        return [Echo(text=text, expands=True)]

    # -- the turn ------------------------------------------------------
    def _session_execution_started(self, d: dict, obj: dict) -> list[Event]:
        ok, parent = self._belongs(d)
        if ok and parent is None and isinstance(obj.get("created"), int):
            self.turn_start = obj["created"]
        return []

    def _end(self, d: dict, obj: dict, error: str | None) -> list[Event]:
        ok, parent = self._belongs(d)
        if not ok or parent is not None:
            return []
        end = obj.get("created")
        span = (
            end - self.turn_start
            if self.turn_start is not None and isinstance(end, int)
            else None
        )
        result = Result(
            is_error=error is not None,
            subtype=error or "success",
            duration_ms=span,
            cost_usd=self.cost,
            stop_reason=None,
            context_window=None,
            error_text=error,
        )
        self.end_turn()
        return [result]

    def _session_execution_succeeded(self, d: dict, obj: dict) -> list[Event]:
        return self._end(d, obj, None)

    def _session_execution_failed(self, d: dict, obj: dict) -> list[Event]:
        return self._end(d, obj, _error(d.get("error")))

    def _session_execution_interrupted(self, d: dict, obj: dict) -> list[Event]:
        return self._end(d, obj, "interrupted")

    def _session_step_ended(self, d: dict, obj: dict) -> list[Event]:
        ok, parent = self._belongs(d)
        if not ok:
            return []
        if isinstance(d.get("cost"), (int, float)):
            self.cost += float(d["cost"])
        u = _usage(d.get("tokens"))
        return [Step(usage=u, parent=parent)] if u is not None else []

    _session_step_failed = _session_step_ended

    # -- text and reasoning --------------------------------------------
    def _block(self, d: dict, kind: str, text: str) -> list[Event]:
        ok, parent = self._belongs(d)
        if not ok:
            return []
        key = entry_key(d, kind)
        if parent is None:
            self.keys.add(key)
        cls = Text if kind == "prose" else Thinking
        return [cls(text=text, parent=parent, usage=None, key=key)]

    def _session_text_started(self, d: dict, obj: dict) -> list[Event]:
        return self._block(d, "prose", "")

    def _session_text_ended(self, d: dict, obj: dict) -> list[Event]:
        return self._block(d, "prose", str(d.get("text") or ""))

    def _session_reasoning_started(self, d: dict, obj: dict) -> list[Event]:
        return self._block(d, "thinking", "")

    def _session_reasoning_ended(self, d: dict, obj: dict) -> list[Event]:
        return self._block(d, "thinking", str(d.get("text") or ""))

    def _delta(self, d: dict, kind: str) -> list[Event]:
        """Always one Delta, so the session never stores a delta line; one with
        no key (a child's, or a block not seen opening) draws nothing."""
        ok, parent = self._belongs(d)
        key = entry_key(d, kind)
        if not ok or parent is not None or key not in self.keys:
            return [Delta(key="", kind="", text="")]
        return [Delta(key=key, kind=kind, text=str(d.get("delta") or ""))]

    def _session_text_delta(self, d: dict, obj: dict) -> list[Event]:
        return self._delta(d, "prose")

    def _session_reasoning_delta(self, d: dict, obj: dict) -> list[Event]:
        return self._delta(d, "thinking")

    # -- tools ---------------------------------------------------------
    def _session_tool_input_started(self, d: dict, obj: dict) -> list[Event]:
        ok, parent = self._belongs(d)
        call, name = str(d.get("id") or ""), str(d.get("name") or "?")
        if ok:
            self.names[call] = name
            if name == "subagent" and parent is None:
                self.waiting.append(call)  # its child may start before it runs
        return []

    def _session_tool_called(self, d: dict, obj: dict) -> list[Event]:
        ok, parent = self._belongs(d)
        call = str(d.get("id") or "")
        if not ok or call in self.open_calls or call in self.closed_calls:
            return []
        self.open_calls.add(call)
        raw = self.names.get(call, "?")
        name = tool_name(raw)
        return [
            ToolCall(
                id=call,
                name=name,
                input=tool_input(raw, d.get("input")),
                parent=parent,
                usage=None,
            )
        ]

    def _session_tool_progress(self, d: dict, obj: dict) -> list[Event]:
        ok, parent = self._belongs(d)
        child = _str(_dict(d.get("metadata")).get("sessionID"))
        call = str(d.get("id") or "")
        if ok and parent is None and child is not None:
            self.children[child] = call
            if call in self.waiting:
                self.waiting.remove(call)
        return []

    def _tool_end(self, d: dict, text: str, is_error: bool) -> list[Event]:
        ok, parent = self._belongs(d)
        call = str(d.get("id") or "")
        if not ok or call in self.closed_calls:
            return []  # its turn already ended
        self.open_calls.discard(call)
        self.closed_calls.add(call)
        if call in self.waiting:
            self.waiting.remove(call)
        return [ToolOutput(id=call, text=text, is_error=is_error, parent=parent)]

    def _session_tool_success(self, d: dict, obj: dict) -> list[Event]:
        return self._tool_end(d, _content(d.get("content")), False)

    def _session_tool_failed(self, d: dict, obj: dict) -> list[Event]:
        err = _dict(d.get("error"))
        text = "interrupted" if err.get("type") == "aborted" else _error(err)
        return self._tool_end(d, text, True)
