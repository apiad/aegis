"""Field-level truncation of an ``encode_event()`` dict for the compact WS
wire. The result stays valid input to ``decode_event`` (extra keys ignored);
the full event is fetched on demand via the ``get_event`` RPC."""
from __future__ import annotations

from aegis.comms.descriptors import aegis_glyph
from aegis.render_shared import describe_tool, tool_glyph
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
        # Precompute the human description AND the glyph server-side
        # (raw_input has the Bash `description` arg etc.), then drop
        # raw_input from the wire — the full args are fetched on demand via
        # get_event when expanded. The glyph goes over the wire rather than
        # being resolved in the browser so there is exactly one glyph table,
        # in Python, for the TUI, the HTML export and the web client.
        out = dict(d)
        name = d.get("name", "")
        raw = d.get("raw_input")
        out["desc"] = describe_tool(name, raw, d.get("summary", ""),
                                    d.get("locations") or ())
        out["icon"] = tool_glyph(name, d.get("kind"), raw)
        out["comms"] = aegis_glyph(name, raw or {}) is not None
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
