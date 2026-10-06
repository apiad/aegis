"""Claude Code's stream-json output, one stdout line at a time, as typed events.

Adapted from the old tree's ``events.py``, keeping only what slice 1 renders.
One assistant line can carry several content blocks, so ``parse`` returns a
list. Valid JSON of a type nothing here handles is ``Ignored``; a line that is
not a JSON object is ``Garbled`` and is shown, never fatal.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Usage:
    input: int
    cache_creation: int
    cache_read: int
    output: int

    @property
    def context(self) -> int:
        """Tokens in the context window after this message."""
        return self.input + self.cache_creation + self.cache_read + self.output


@dataclass(frozen=True)
class Init:
    session_id: str | None
    model: str | None
    version: str | None


@dataclass(frozen=True)
class Text:
    text: str
    parent: str | None
    usage: Usage | None


@dataclass(frozen=True)
class Thinking:
    text: str
    parent: str | None
    usage: Usage | None


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict
    parent: str | None
    usage: Usage | None


@dataclass(frozen=True)
class ToolOutput:
    id: str
    text: str
    is_error: bool
    parent: str | None


@dataclass(frozen=True)
class Echo:
    """A prompt Claude has read, echoed back by ``--replay-user-messages``."""

    text: str


@dataclass(frozen=True)
class Result:
    is_error: bool
    subtype: str
    duration_ms: int | None
    cost_usd: float | None
    stop_reason: str | None
    context_window: int | None


@dataclass(frozen=True)
class Notice:
    """A system line that is not part of any turn: hooks, thinking-token
    estimates, task notices. It never moves a session to working.

    Claude's own tasks (a Bash call, a background command) are reported as
    ``task_started`` and closed by ``task_notification`` with a ``status``;
    a session's open tasks are the ids started and not yet notified."""

    subtype: str
    task_id: str | None = None
    status: str | None = None


@dataclass(frozen=True)
class Compact:
    pre_tokens: int
    post_tokens: int


@dataclass(frozen=True)
class Ignored:
    type: str


@dataclass(frozen=True)
class Garbled:
    raw: str


Event = (
    Init
    | Text
    | Thinking
    | ToolCall
    | ToolOutput
    | Echo
    | Result
    | Notice
    | Compact
    | Ignored
    | Garbled
)

# Events that only occur inside a turn. Anything else on the wire (system
# notices above all) arrives between turns too, and promoting it to a turn
# parked old-tree sessions on a read that never returned.
TURN_BEARING = (Text, Thinking, ToolCall, ToolOutput)


def _usage(d: Any) -> Usage | None:
    if not isinstance(d, dict):
        return None
    keys = (
        "input_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "output_tokens",
    )
    if not any(k in d for k in keys):
        return None
    return Usage(
        input=int(d.get("input_tokens") or 0),
        cache_creation=int(d.get("cache_creation_input_tokens") or 0),
        cache_read=int(d.get("cache_read_input_tokens") or 0),
        output=int(d.get("output_tokens") or 0),
    )


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def _str(v: Any) -> str | None:
    return v if isinstance(v, str) else None


def parse(line: str) -> list[Event]:
    try:
        obj: Any = json.loads(line)
    except ValueError:
        return [Garbled(raw=line)]
    if not isinstance(obj, dict):
        return [Garbled(raw=line)]
    etype = str(obj.get("type"))
    parent = _str(obj.get("parent_tool_use_id"))

    if etype == "system":
        sub = str(obj.get("subtype"))
        if sub == "init":
            return [
                Init(
                    session_id=_str(obj.get("session_id")),
                    model=_str(obj.get("model")),
                    version=_str(obj.get("claude_code_version")),
                )
            ]
        if sub == "compact_boundary":
            meta = obj.get("compact_metadata")
            meta = meta if isinstance(meta, dict) else {}
            return [
                Compact(
                    pre_tokens=int(meta.get("pre_tokens") or 0),
                    post_tokens=int(meta.get("post_tokens") or 0),
                )
            ]
        return [
            Notice(
                subtype=sub,
                task_id=_str(obj.get("task_id")),
                status=_str(obj.get("status")),
            )
        ]

    if etype == "result":
        window = None
        mu = obj.get("modelUsage")
        if isinstance(mu, dict):
            for u in mu.values():
                if isinstance(u, dict) and isinstance(u.get("contextWindow"), int):
                    window = max(window or 0, u["contextWindow"])
        cost = obj.get("total_cost_usd")
        dur = obj.get("duration_ms")
        return [
            Result(
                is_error=bool(obj.get("is_error", False)),
                subtype=str(obj.get("subtype") or ""),
                duration_ms=dur if isinstance(dur, int) else None,
                cost_usd=float(cost) if isinstance(cost, (int, float)) else None,
                stop_reason=_str(obj.get("stop_reason")),
                context_window=window,
            )
        ]

    message = obj.get("message")
    message = message if isinstance(message, dict) else {}
    content = message.get("content")

    if etype == "assistant" and isinstance(content, list):
        usage = _usage(message.get("usage"))
        out: list[Event] = []
        for b in content:
            if not isinstance(b, dict):
                continue
            bt = b.get("type")
            if bt == "text":
                out.append(
                    Text(text=str(b.get("text", "")), parent=parent, usage=usage)
                )
            elif bt == "thinking":
                out.append(
                    Thinking(
                        text=str(b.get("thinking", "")), parent=parent, usage=usage
                    )
                )
            elif bt == "tool_use":
                inp = b.get("input")
                out.append(
                    ToolCall(
                        id=str(b.get("id", "")),
                        name=str(b.get("name", "?")),
                        input=inp if isinstance(inp, dict) else {},
                        parent=parent,
                        usage=usage,
                    )
                )
        return out if out else [Ignored(type=etype)]

    if etype == "user":
        if isinstance(content, list):
            outs: list[Event] = [
                ToolOutput(
                    id=str(b.get("tool_use_id", "")),
                    text=_text_of(b.get("content", "")),
                    is_error=bool(b.get("is_error", False)),
                    parent=parent,
                )
                for b in content
                if isinstance(b, dict) and b.get("type") == "tool_result"
            ]
            if outs:
                return outs
        # isReplay marks the user's own prompt and nothing else: skill bodies
        # and subagent prompts also arrive as role:user (old tree, measured
        # over 269 transcripts with no false positive).
        if obj.get("isReplay") is True:
            text = _text_of(content).strip()
            if text:
                return [Echo(text=text)]
        return [Ignored(type=etype)]

    return [Ignored(type=etype)]
