"""OpenCode JSON parser.

Maps OpenCode's ``--format json`` line-by-line JSON events into aegis's
canonical ``Event`` union.

Observed event shapes (opencode v1.x):

    {"type":"step_start","sessionID":"...","part":{...}}
    {"type":"text",      "sessionID":"...","part":{"text":"..."}}
    {"type":"tool",      "sessionID":"...","part":{"name":"...","args":{...}}}
    {"type":"step_finish","sessionID":"...","part":{"reason":"stop"|"error",
                                                    "tokens":{...}}}
    {"type":"error",     "sessionID":"...","error":{"name":"...","data":{...}}}

OpenCode's terminal event is ``step_finish`` (or ``error``); we surface
either as ``Result`` so the session's ``events()`` iterator stops cleanly.
"""
from __future__ import annotations

import json

from aegis.events import (
    AssistantText, Result, SystemInit, ToolUse, Unknown,
)


def parse(line: str) -> AssistantText | Result | SystemInit | ToolUse | Unknown:
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return Unknown(raw=line)
    if not isinstance(obj, dict):
        return Unknown(raw=line)

    etype = obj.get("type")
    session_id = obj.get("sessionID")
    part = obj.get("part") if isinstance(obj.get("part"), dict) else {}

    if etype == "step_start":
        return SystemInit(session_id=session_id)

    if etype == "text":
        text = part.get("text") if isinstance(part, dict) else None
        if isinstance(text, str):
            return AssistantText(text=text)
        return Unknown(raw=line)

    if etype == "tool":
        name = part.get("name", "?") if isinstance(part, dict) else "?"
        args = part.get("args") or {} if isinstance(part, dict) else {}
        summary = ""
        if isinstance(args, dict):
            for v in args.values():
                if isinstance(v, str):
                    summary = v
                    break
        return ToolUse(name=name, summary=summary)

    if etype == "step_finish":
        reason = part.get("reason") if isinstance(part, dict) else None
        is_error = reason not in ("stop", "end", None)
        tokens = part.get("tokens") or {} if isinstance(part, dict) else {}
        if not isinstance(tokens, dict):
            tokens = {}
        return Result(
            duration_ms=None,
            is_error=is_error,
            input_tokens=tokens.get("input"),
            output_tokens=tokens.get("output"),
        )

    if etype == "error":
        return Result(
            duration_ms=None,
            is_error=True,
            input_tokens=None,
            output_tokens=None,
        )

    return Unknown(raw=line)
