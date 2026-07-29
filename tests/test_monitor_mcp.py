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


async def test_monitor_defaults_to_the_project_root(tmp_path):
    mm = _mm()
    bridge = _Bridge(mm)
    bridge.state_root = str(tmp_path)
    server = build_server(bridge)
    out = await _call(server, "aegis_monitor", from_handle="p",
                      description="x", done="chk", interval_s=999)
    assert mm._monitors[out["monitor_id"]].cwd == str(tmp_path)


async def test_monitor_cwd_is_settable_and_relative_to_the_root(tmp_path):
    """Conditions run in the given directory. Agents work inside
    repos/<name>, so a monitor pinned to the project root evaluates its
    bash somewhere the agent never meant — silently, until it times out."""
    mm = _mm()
    bridge = _Bridge(mm)
    bridge.state_root = str(tmp_path)
    (tmp_path / "repos" / "aegis").mkdir(parents=True)
    server = build_server(bridge)

    rel = await _call(server, "aegis_monitor", from_handle="p",
                      description="x", done="chk", cwd="repos/aegis",
                      interval_s=999)
    assert mm._monitors[rel["monitor_id"]].cwd == str(tmp_path / "repos" / "aegis")

    absolute = await _call(server, "aegis_monitor", from_handle="p",
                           description="x", done="chk",
                           cwd=str(tmp_path / "repos"), interval_s=999)
    assert mm._monitors[absolute["monitor_id"]].cwd == str(tmp_path / "repos")


async def test_monitor_rejects_a_cwd_that_does_not_exist(tmp_path):
    """Every poll would fail in a missing directory, so the monitor would
    just sit there and time out — say so now instead."""
    mm = _mm()
    bridge = _Bridge(mm)
    bridge.state_root = str(tmp_path)
    server = build_server(bridge)
    out = await _call(server, "aegis_monitor", from_handle="p",
                      description="x", done="chk", cwd="nope/not/here")
    assert "error" in out
    assert "monitor_id" not in out
    assert mm._monitors == {}


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


def test_briefing_and_priming_push_a_progress_condition():
    """`progress` is what turns the strip from a spinner into a bar + ETA,
    so both texts have to ask for one rather than list it as optional."""
    assert "progress" in PRIMING
    for text in (BRIEFING, PRIMING):
        assert "ALWAYS pass `progress`" in text
