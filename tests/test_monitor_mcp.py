"""aegis_monitor / aegis_monitors / aegis_monitor_cancel MCP surface."""
from __future__ import annotations

from aegis.mcp.server import BRIEFING, PRIMING, build_server
from aegis.monitor.manager import MonitorManager
from aegis.queue.inbox import InboxRouter


class _Bridge:
    def __init__(self, mm: MonitorManager) -> None:
        self.monitor_manager = mm
        self.state_root = "/tmp"


def _mm():
    async def run_bash(cmd, cwd):
        return (0, "") if cmd == "chk-done" else (1, "")
    return MonitorManager(InboxRouter(), run_bash=run_bash,
                          now=lambda: "2026-07-20T00:00:00Z")


async def _call(server, tool_name: str, **kwargs):
    res = await server.call_tool(tool_name, kwargs)
    if getattr(res, "structured_content", None) is not None:
        sc = res.structured_content
        if isinstance(sc, dict) and set(sc.keys()) == {"result"}:
            return sc["result"]
        return sc
    return getattr(res, "data", res)


async def test_monitor_tools_are_registered():
    server = build_server(_Bridge(_mm()))
    names = {t.name for t in await server.list_tools()}
    assert {"aegis_monitor", "aegis_monitors",
            "aegis_monitor_cancel"} <= names


async def test_aegis_monitor_starts_and_lists():
    mm = _mm()
    server = build_server(_Bridge(mm))
    out = await _call(server, "aegis_monitor", from_handle="p",
                      description="pytest", done="chk-done", interval_s=999)
    mid = out["monitor_id"]
    listed = await _call(server, "aegis_monitors")
    assert any(row["id"] == mid for row in listed)


async def test_aegis_monitor_cancel():
    mm = _mm()
    server = build_server(_Bridge(mm))
    out = await _call(server, "aegis_monitor", from_handle="p",
                      description="x", done="never")
    res = await _call(server, "aegis_monitor_cancel",
                      monitor_id=out["monitor_id"])
    assert res["state"] == "cancelled"


def test_briefing_and_priming_mention_monitor():
    assert "aegis_monitor" in BRIEFING
    assert "aegis_monitor" in PRIMING
