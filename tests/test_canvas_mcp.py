"""End-to-end MCP-tool tests for the canvas plane.

We exercise the FastMCP server through its tool registry — the same
path agents hit at runtime — against a stub AppBridge carrying a real
CanvasManager + InboxRouter.
"""
from __future__ import annotations

import pytest

from aegis.canvas.manager import CanvasManager
from aegis.canvas.notify import make_canvas_notifier
from aegis.mcp.server import build_server
from aegis.queue.inbox import InboxRouter


class _StubBridge:
    def __init__(self, canvas_manager, inbox_router):
        self.canvas_manager = canvas_manager
        self.inbox_router = inbox_router
        self.queue_manager = None

    def list_sessions(self): return []
    def list_agents(self): return []
    async def handoff(self, *a, **kw): return "noop"
    async def spawn(self, *a, **kw): return "noop"
    async def close(self, handle): return None


async def _call(server, tool_name, **kwargs):
    """Look up a registered tool by name and call it."""
    res = await server.call_tool(tool_name, kwargs)
    # FastMCP returns a ToolResult; extract the structured payload.
    if hasattr(res, "structured_content") and res.structured_content is not None:
        sc = res.structured_content
        # Single non-dict returns get wrapped as {"result": value}
        if isinstance(sc, dict) and set(sc.keys()) == {"result"}:
            return sc["result"]
        return sc
    if hasattr(res, "data"):
        return res.data
    return res


@pytest.fixture
def bridge(tmp_path):
    router = InboxRouter(state_dir=tmp_path / "router")
    mgr = CanvasManager(state_dir=tmp_path / ".aegis" / "state",
                        notifier=make_canvas_notifier(router))
    return _StubBridge(mgr, router), router, tmp_path


@pytest.mark.asyncio
async def test_canvas_open_creates_file_and_returns_metadata(bridge):
    b, _, tmp = bridge
    server = build_server(b)
    f = tmp / "r.md"
    info = await _call(server, "aegis_canvas_open",
                       name="report", file=str(f),
                       from_handle="alice")
    assert info["name"] == "report"
    assert info["file"] == str(f)
    assert info["sections"] == []
    assert f.exists()


@pytest.mark.asyncio
async def test_canvas_open_without_file_first_time_errors(bridge):
    b, _, _ = bridge
    server = build_server(b)
    out = await _call(server, "aegis_canvas_open",
                      name="ghost", from_handle="alice")
    assert "error" in out


@pytest.mark.asyncio
async def test_write_then_read_roundtrip(bridge):
    b, _, tmp = bridge
    server = build_server(b)
    await _call(server, "aegis_canvas_open",
                name="r", file=str(tmp / "r.md"), from_handle="alice")
    res = await _call(server, "aegis_canvas_write_section",
                      name="r", section="intro", content="hello",
                      from_handle="alice")
    assert res["ok"] is True
    assert res["section"] == "intro"
    assert res["op"] == "write"
    assert res["added"] == 1

    full = await _call(server, "aegis_canvas_read",
                       name="r", from_handle="alice")
    assert "## intro" in full["content"]
    section = await _call(server, "aegis_canvas_read",
                          name="r", section="intro", from_handle="alice")
    assert section["content"] == "hello"


@pytest.mark.asyncio
async def test_append_grows_section(bridge):
    b, _, tmp = bridge
    server = build_server(b)
    await _call(server, "aegis_canvas_open",
                name="r", file=str(tmp / "r.md"), from_handle="alice")
    await _call(server, "aegis_canvas_write_section",
                name="r", section="log", content="line1",
                from_handle="alice")
    res = await _call(server, "aegis_canvas_append_to_section",
                      name="r", section="log", text="line2",
                      from_handle="bob")
    assert res["op"] == "append"
    body = await _call(server, "aegis_canvas_read",
                       name="r", section="log", from_handle="alice")
    assert body["content"] == "line1\nline2"


@pytest.mark.asyncio
async def test_subscribe_then_other_agent_write_notifies(bridge):
    b, router, tmp = bridge
    server = build_server(b)
    await _call(server, "aegis_canvas_open",
                name="r", file=str(tmp / "r.md"), from_handle="alice")
    sub = await _call(server, "aegis_canvas_subscribe",
                      name="r", from_handle="bob")
    assert sub["ok"] is True
    assert "bob" in sub["subscribers"]
    # alice writes — bob gets a notification, alice does not
    await _call(server, "aegis_canvas_write_section",
                name="r", section="intro", content="hello",
                from_handle="alice")
    bob_inbox = router.pending("bob")
    assert len(bob_inbox) == 1
    assert bob_inbox[0].sender == "canvas:r"
    assert "intro" in bob_inbox[0].body
    assert "alice" in bob_inbox[0].body
    assert router.pending("alice") == []


@pytest.mark.asyncio
async def test_subscribe_filter_only_fires_for_matching_section(bridge):
    b, router, tmp = bridge
    server = build_server(b)
    await _call(server, "aegis_canvas_open",
                name="r", file=str(tmp / "r.md"), from_handle="alice")
    await _call(server, "aegis_canvas_subscribe",
                name="r", from_handle="bob", sections=["data"])
    # write to intro — bob NOT notified
    await _call(server, "aegis_canvas_write_section",
                name="r", section="intro", content="hi", from_handle="alice")
    assert router.pending("bob") == []
    # write to data — bob IS notified
    await _call(server, "aegis_canvas_write_section",
                name="r", section="data", content="numbers", from_handle="alice")
    assert len(router.pending("bob")) == 1


@pytest.mark.asyncio
async def test_unsubscribe_stops_notifications(bridge):
    b, router, tmp = bridge
    server = build_server(b)
    await _call(server, "aegis_canvas_open",
                name="r", file=str(tmp / "r.md"), from_handle="alice")
    await _call(server, "aegis_canvas_subscribe",
                name="r", from_handle="bob")
    await _call(server, "aegis_canvas_unsubscribe",
                name="r", from_handle="bob")
    await _call(server, "aegis_canvas_write_section",
                name="r", section="intro", content="hi", from_handle="alice")
    assert router.pending("bob") == []


@pytest.mark.asyncio
async def test_list_canvases_returns_all_open(bridge):
    b, _, tmp = bridge
    server = build_server(b)
    await _call(server, "aegis_canvas_open",
                name="a", file=str(tmp / "a.md"), from_handle="alice")
    await _call(server, "aegis_canvas_open",
                name="b", file=str(tmp / "b.md"), from_handle="alice")
    lst = await _call(server, "aegis_canvas_list")
    names = sorted(e["name"] for e in lst)
    assert names == ["a", "b"]


@pytest.mark.asyncio
async def test_write_invalid_section_returns_error(bridge):
    b, _, tmp = bridge
    server = build_server(b)
    await _call(server, "aegis_canvas_open",
                name="r", file=str(tmp / "r.md"), from_handle="alice")
    out = await _call(server, "aegis_canvas_write_section",
                      name="r", section="bad/name", content="x",
                      from_handle="alice")
    assert "error" in out


@pytest.mark.asyncio
async def test_two_agents_collaborate_on_report(bridge):
    """Worked-example flow from the spec: PM opens, subscribes,
    writes intro; researcher subscribes to 'data' only, writes data,
    PM gets notified."""
    b, router, tmp = bridge
    server = build_server(b)
    f = tmp / "report.md"
    # PM opens + subscribes to all + writes intro
    await _call(server, "aegis_canvas_open",
                name="report", file=str(f), from_handle="pm")
    await _call(server, "aegis_canvas_subscribe",
                name="report", from_handle="pm")
    await _call(server, "aegis_canvas_write_section",
                name="report", section="intro",
                content="Q3 was a quarter of consolidation.",
                from_handle="pm")
    # Researcher opens + subscribes to data + writes data
    await _call(server, "aegis_canvas_open",
                name="report", from_handle="researcher")
    await _call(server, "aegis_canvas_subscribe",
                name="report", from_handle="researcher",
                sections=["data"])
    await _call(server, "aegis_canvas_write_section",
                name="report", section="data",
                content="Q3 numbers came in stronger than projected.",
                from_handle="researcher")
    # PM has exactly one inbox message (researcher's write); PM's own
    # write did not echo back
    pm_inbox = router.pending("pm")
    assert len(pm_inbox) == 1
    assert "researcher" in pm_inbox[0].body
    assert "data" in pm_inbox[0].body
    # Researcher has no inbox (they wrote, but their filter was data
    # and the only write to data was their own — self-suppressed)
    assert router.pending("researcher") == []
    # File on disk has both sections
    text = f.read_text()
    assert "## intro" in text and "## data" in text
    assert "consolidation" in text and "stronger" in text
