"""The browser gets the glyph off the wire, so there is one glyph table."""
from __future__ import annotations

import re
from pathlib import Path

from aegis.web.compact import compact_encoded

_JS = Path(__file__).resolve().parent.parent / (
    "src/aegis/web/static/js/renderEvent.js")


def _tool_use(name: str, raw_input: dict, kind: str | None = None) -> dict:
    return {"t": "ToolUse", "name": name, "raw_input": raw_input,
            "kind": kind, "summary": "", "locations": []}


def test_the_wire_carries_the_resolved_aegis_glyph():
    out, changed = compact_encoded(_tool_use(
        "mcp__aegis__aegis_handoff",
        {"target_handle": "weary-turing", "context": "the render is yours"}))
    assert changed
    assert out["icon"] == "⇄"
    assert out["comms"] is True
    assert out["desc"] == 'weary-turing · "the render is yours"'
    assert "raw_input" not in out


def test_the_wire_carries_the_native_emoji_too():
    out, _ = compact_encoded(_tool_use("Read", {"file_path": "/tmp/a.py"},
                                       kind="read"))
    assert out["icon"] == "📖"
    assert out["comms"] is False


def test_the_browser_no_longer_keeps_its_own_glyph_table():
    """One table, in Python. The duplicate drifted the moment a glyph was
    added on one side only — which is exactly what this feature would have
    done to it."""
    src = _JS.read_text(encoding="utf-8")
    assert "KIND_ICON" not in src


def test_the_browser_paints_aegis_calls_with_their_own_class():
    src = _JS.read_text(encoding="utf-8")
    assert re.search(r"ev\.comms", src)
    assert "tool-use comms" in src
