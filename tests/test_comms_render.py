"""The renderers speak the registry's language."""
from __future__ import annotations

from rich.cells import cell_len
from rich.console import Console

from aegis.events import ToolUse
from aegis.render import render_tool_use
from aegis.render_html import render_event_html
from aegis.render_shared import describe_tool
from aegis.themes import load_theme


def _tool_use(name: str, raw_input: dict, kind: str | None = None) -> ToolUse:
    """``kind`` is set by the stream parser, not the constructor — a native
    tool without it would fall to the generic dot for reasons unrelated to
    this feature."""
    return ToolUse(name=name, summary="", raw_input=raw_input,
                   tool_call_id="t1", kind=kind)


def test_describe_tool_prefers_the_aegis_registry():
    line = describe_tool("mcp__aegis__aegis_handoff", {
        "from_handle": "me", "target_handle": "weary-turing",
        "context": "the render is yours"})
    assert line == 'weary-turing · "the render is yours"'


def test_describe_tool_leaves_native_tools_alone():
    assert describe_tool("Bash", {"description": "run tests",
                                  "command": "pytest -q"}) == (
        "run tests  ·  pytest -q")


def test_the_old_fallback_no_longer_leaks_arguments():
    """Before this feature a handoff rendered as its first stringy arg —
    the calling agent's own handle, which says nothing about the call."""
    line = describe_tool("mcp__aegis__aegis_handoff", {
        "from_handle": "me", "target_handle": "weary-turing",
        "context": "x"})
    assert not line.startswith("me")


def _plain(renderable) -> str:
    console = Console(width=120, no_color=True)
    with console.capture() as cap:
        console.print(renderable)
    return cap.get()


def test_render_tool_use_uses_the_aegis_glyph():
    colors = load_theme("aegis-ink").to_aegis_colors()
    out = _plain(render_tool_use(
        _tool_use("mcp__aegis__aegis_claim",
                  {"paths": ["src/aegis/mcp/"], "intent": "exclusive"}),
        colors))
    assert out.startswith("⊙ ")
    assert "exclusive · src/aegis/mcp/" in out


def test_a_native_tool_keeps_its_emoji():
    colors = load_theme("aegis-ink").to_aegis_colors()
    out = _plain(render_tool_use(
        _tool_use("Read", {"file_path": "/tmp/a.py"}, kind="read"), colors))
    assert out.startswith("📖 ")


def test_the_glyph_is_always_followed_by_a_space():
    """East Asian Ambiguous: Rich measures one cell, terminals draw wider.
    Without the separator the glyph overlaps its neighbour."""
    colors = load_theme("aegis-ink").to_aegis_colors()
    for name, args in [("aegis_handoff", {"target_handle": "p"}),
                       ("aegis_claim", {"paths": ["a"]}),
                       ("aegis_meta", {})]:
        out = _plain(render_tool_use(_tool_use(name, args), colors))
        assert out[1] == " ", f"{name} rendered {out[:4]!r}"
        assert cell_len(out[0]) == 1


def test_html_export_uses_the_same_glyph_and_line():
    html = render_event_html(
        _tool_use("mcp__aegis__aegis_enqueue",
                  {"queue": "general", "payload": "port the fixtures"}))
    assert "⇉" in html
    assert 'general · &quot;port the fixtures&quot;' in html or (
        'general · "port the fixtures"' in html)
