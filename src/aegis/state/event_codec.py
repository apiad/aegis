"""Serialize aegis Event dataclasses to/from JSON-safe dicts.

Used by session_log to persist a tab's event stream for local
transcript redraw on resume. Type tag is the dataclass name under
``t``; field names mirror the dataclass.
"""
from __future__ import annotations

from aegis.events import (
    AssistantText, AssistantThinking, Event, Result, SystemInit,
    TokenUsage, ToolResult, ToolUse, Unknown,
)


def _encode_usage(u: TokenUsage | None) -> dict | None:
    if u is None:
        return None
    return {"input": u.input, "cache_creation": u.cache_creation,
            "cache_read": u.cache_read, "output": u.output}


def _decode_usage(d: dict | None) -> TokenUsage | None:
    if d is None:
        return None
    return TokenUsage(input=d["input"],
                      cache_creation=d["cache_creation"],
                      cache_read=d["cache_read"],
                      output=d["output"])


def encode_event(ev: Event) -> dict:
    if isinstance(ev, SystemInit):
        return {"t": "SystemInit", "session_id": ev.session_id}
    if isinstance(ev, AssistantText):
        return {"t": "AssistantText", "text": ev.text,
                "usage": _encode_usage(ev.usage)}
    if isinstance(ev, AssistantThinking):
        return {"t": "AssistantThinking", "text": ev.text,
                "usage": _encode_usage(ev.usage)}
    if isinstance(ev, ToolUse):
        out = {"t": "ToolUse", "name": ev.name, "summary": ev.summary,
               "usage": _encode_usage(ev.usage)}
        if ev.kind is not None:
            out["kind"] = ev.kind
        if ev.tool_call_id is not None:
            out["tool_call_id"] = ev.tool_call_id
        if ev.raw_input is not None:
            out["raw_input"] = ev.raw_input
        if ev.locations:
            out["locations"] = [[p, ln] for p, ln in ev.locations]
        if ev.status is not None:
            out["status"] = ev.status
        return out
    if isinstance(ev, ToolResult):
        out = {"t": "ToolResult", "text": ev.text,
               "is_error": ev.is_error}
        if ev.tool_call_id is not None:
            out["tool_call_id"] = ev.tool_call_id
        if ev.kind is not None:
            out["kind"] = ev.kind
        return out
    if isinstance(ev, Result):
        return {"t": "Result", "duration_ms": ev.duration_ms,
                "is_error": ev.is_error,
                "input_tokens": ev.input_tokens,
                "output_tokens": ev.output_tokens,
                "usage": _encode_usage(ev.usage)}
    if isinstance(ev, Unknown):
        return {"t": "Unknown", "raw": ev.raw}
    raise ValueError(f"unknown event type: {type(ev).__name__}")


def decode_event(d: dict) -> Event:
    t = d.get("t")
    if t is None:
        raise ValueError("event dict missing type tag 't'")
    if t == "SystemInit":
        return SystemInit(session_id=d.get("session_id"))
    if t == "AssistantText":
        return AssistantText(text=d["text"], usage=_decode_usage(d.get("usage")))
    if t == "AssistantThinking":
        return AssistantThinking(text=d["text"],
                                 usage=_decode_usage(d.get("usage")))
    if t == "ToolUse":
        locs = tuple((p, ln) for p, ln in d.get("locations", []))
        return ToolUse(name=d["name"], summary=d["summary"],
                       usage=_decode_usage(d.get("usage")),
                       kind=d.get("kind"),
                       raw_input=d.get("raw_input"),
                       tool_call_id=d.get("tool_call_id"),
                       locations=locs,
                       status=d.get("status"))
    if t == "ToolResult":
        return ToolResult(text=d["text"], is_error=d["is_error"],
                          tool_call_id=d.get("tool_call_id"),
                          kind=d.get("kind"))
    if t == "Result":
        return Result(duration_ms=d.get("duration_ms"),
                      is_error=d["is_error"],
                      input_tokens=d.get("input_tokens"),
                      output_tokens=d.get("output_tokens"),
                      usage=_decode_usage(d.get("usage")))
    if t == "Unknown":
        return Unknown(raw=d["raw"])
    raise ValueError(f"unknown event type tag: {t!r}")
