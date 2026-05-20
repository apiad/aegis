"""Gemini CLI stream-json parser.

Maps Gemini's ``--output-format stream-json`` line-by-line JSON events
into aegis's canonical ``Event`` union (``SystemInit``, ``AssistantText``,
``ToolUse``, ``Result``, ``Unknown``).

Gemini's event shapes (observed against gemini CLI v1.x):

    {"type":"init",   "session_id":"...", "model":"..."}
    {"type":"message","role":"user",      "content":"..."}
    {"type":"message","role":"assistant", "content":"...", "delta":true}
    {"type":"tool_call","name":"...",     "args":{...}}
    {"type":"result", "status":"success"|"error", "stats":{...}}

User-role messages are the substrate echoing the prompt back — we return
``Unknown`` for them so the consuming session can ignore them without a
special filter. Tool-result events have not been observed in Gemini's
stream-json output yet; when they appear we'll add a branch (mirrors the
existing Claude parser shape).
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

    if etype == "init":
        return SystemInit(session_id=obj.get("session_id"))

    if etype == "message":
        role = obj.get("role")
        if role == "assistant":
            content = obj.get("content")
            if isinstance(content, str):
                return AssistantText(text=content)
        # User-role messages are the substrate echoing — not an event
        # the agent layer needs. Drop as Unknown.
        return Unknown(raw=line)

    if etype == "tool_call":
        name = obj.get("name", "?")
        args = obj.get("args") or {}
        summary = ""
        if isinstance(args, dict):
            # Pick a string-valued arg as the one-liner summary
            # (matches the Claude parser's heuristic).
            for v in args.values():
                if isinstance(v, str):
                    summary = v
                    break
        return ToolUse(name=name, summary=summary)

    if etype == "result":
        status = obj.get("status")
        is_error = status != "success"
        stats = obj.get("stats") or {}
        if not isinstance(stats, dict):
            stats = {}
        return Result(
            duration_ms=stats.get("duration_ms"),
            is_error=is_error,
            input_tokens=stats.get("input_tokens"),
            output_tokens=stats.get("output_tokens"),
        )

    return Unknown(raw=line)
