"""Field-level truncation of an ``encode_event()`` dict for the compact WS
wire. The result stays valid input to ``decode_event`` (extra keys ignored);
the full event is fetched on demand via the ``get_event`` RPC."""
from __future__ import annotations

from aegis.transcript_constants import TOOL_RESULT_HEAD_LINES


def _clip_lines(text: str, n: int) -> tuple[str, bool]:
    lines = text.splitlines()
    if len(lines) <= n:
        return text, False
    return "\n".join(lines[:n]), True


def compact_encoded(d: dict) -> tuple[dict, bool]:
    t = d.get("t")
    if t == "ToolResult":
        text = d.get("text") or ""
        clipped, was = _clip_lines(text, TOOL_RESULT_HEAD_LINES)
        if not was:
            return d, False
        out = dict(d)
        out["text"] = clipped
        out["full_len"] = len(text)
        return out, True
    if t == "ToolUse":
        if d.get("raw_input") is None:
            return d, False
        out = dict(d)
        out.pop("raw_input", None)
        return out, True
    if t == "AssistantThinking":
        text = d.get("text") or ""
        if not text:
            return d, False
        out = dict(d)
        out["text"] = ""
        out["full_len"] = len(text)
        return out, True
    return d, False
