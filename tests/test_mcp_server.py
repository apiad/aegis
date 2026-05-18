import asyncio
import json

from aegis.mcp.server import (
    BRIEFING,
    PRIMING,
    aegis_meta,
    build_server,
    mcp_config_json,
)


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


def test_build_server_registers_only_aegis_meta():
    srv = build_server()
    tools = asyncio.run(srv.list_tools())
    assert {t.name for t in tools} == {"aegis_meta"}
