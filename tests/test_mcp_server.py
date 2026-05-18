import asyncio
import json

import pytest

from aegis.mcp.bridge import SessionInfo
from aegis.mcp.server import (
    BRIEFING,
    PRIMING,
    aegis_meta,
    build_server,
    mcp_config_json,
)


class FakeBridge:
    def __init__(self):
        self._sessions = [
            SessionInfo(handle="lucid-knuth", agent_slug="default",
                        state="ready", active=True, unseen=False)]
        self.delivered = None

    def list_sessions(self):
        return list(self._sessions)

    def list_agents(self):
        return ["default", "fast"]

    async def handoff(self, a, b, c):
        if a == b:
            return "handoff rejected: cannot hand off to yourself"
        if b != "lucid-knuth":
            return (f"handoff rejected: no session {b!r} "
                    f"(use aegis_list_sessions)")
        self.delivered = (a, b, c)
        return f"delivered to {b}"


async def _call(server, name, **kwargs):
    tools = await server.list_tools()
    tool = next(t for t in tools if t.name == name)
    result = await tool.run(kwargs)
    # fastmcp 3.x wraps results in ToolResult; unwrap structured_content.
    sc = getattr(result, "structured_content", None)
    if isinstance(sc, dict) and "result" in sc:
        return sc["result"]
    if sc is not None:
        return sc
    # Fallback: parse the text content (e.g. plain-string returns).
    return result.content[0].text


def test_briefing_has_orientation_phrases():
    b = BRIEFING.lower()
    for phrase in ("aegis", "meta-harness", "aegis_meta",
                   "only mcp server"):
        assert phrase in b, phrase
    assert aegis_meta() == BRIEFING


def test_priming_points_at_aegis_meta():
    p = PRIMING.lower()
    assert "aegis" in p and "aegis_meta" in p and "first" in p


def test_mcp_config_json_shape():
    cfg = json.loads(mcp_config_json("http://127.0.0.1:9/mcp/"))
    s = cfg["mcpServers"]["aegis"]
    assert s["type"] == "http"
    assert s["url"] == "http://127.0.0.1:9/mcp/"


def test_build_server_registers_all_aegis_tools():
    srv = build_server(FakeBridge())
    tools = asyncio.run(srv.list_tools())
    assert {t.name for t in tools} == {
        "aegis_meta", "aegis_list_sessions",
        "aegis_list_agents", "aegis_handoff"}


@pytest.mark.asyncio
async def test_list_tools_serialise():
    br = FakeBridge()
    srv = build_server(br)
    sess = await _call(srv, "aegis_list_sessions")
    assert isinstance(sess, list)
    assert sess[0]["handle"] == "lucid-knuth"
    assert sess[0]["state"] == "ready"
    assert sess[0]["active"] is True
    agents = await _call(srv, "aegis_list_agents")
    assert agents == ["default", "fast"]


@pytest.mark.asyncio
async def test_handoff_paths():
    br = FakeBridge()
    srv = build_server(br)
    assert "delivered to lucid-knuth" in await _call(
        srv, "aegis_handoff", from_handle="wry-hopper",
        target_handle="lucid-knuth", context="ctx")
    assert br.delivered == ("wry-hopper", "lucid-knuth", "ctx")
    assert "yourself" in await _call(
        srv, "aegis_handoff", from_handle="x", target_handle="x",
        context="c")
    assert "no session" in await _call(
        srv, "aegis_handoff", from_handle="x", target_handle="ghost",
        context="c")


def test_meta_and_priming_updated():
    b = BRIEFING.lower()
    for t in ("aegis_list_sessions", "aegis_list_agents",
              "aegis_handoff"):
        assert t in b
    assert "{handle}" in PRIMING
    assert "aegis_meta" in PRIMING
    assert "your aegis handle" in PRIMING.lower()
