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


# ----- the anti-stale roster on the MCP surface ---------------------------

async def test_arming_a_monitor_reports_the_ones_already_running():
    """The notice that would have caught une-tools-release's orphan: it armed
    two monitors while a dead-process one sat at 60%, and never looked."""
    mm = _mm()
    server = build_server(_Bridge(mm))
    first = await _call(server, "aegis_monitor", from_handle="p",
                        description="suite limpia", done="never",
                        interval_s=999)
    second = await _call(server, "aegis_monitor", from_handle="p",
                         description="diagnóstico", done="never",
                         interval_s=999)
    assert [r["id"] for r in second["also_watching"]] == [first["monitor_id"]]
    assert second["also_watching"][0]["description"] == "suite limpia"
    assert "aegis_monitor_cancel" in second["note"]
    assert "2" in second["note"]  # "You now have 2 live monitors"


async def test_the_first_monitor_gets_no_roster_noise():
    mm = _mm()
    server = build_server(_Bridge(mm))
    out = await _call(server, "aegis_monitor", from_handle="p",
                      description="x", done="never", interval_s=999)
    assert "also_watching" not in out
    assert "note" not in out
    assert set(out) == {"monitor_id"}


async def test_the_roster_at_arming_time_is_scoped_to_the_arming_agent():
    mm = _mm()
    server = build_server(_Bridge(mm))
    await _call(server, "aegis_monitor", from_handle="peer",
                description="theirs", done="never", interval_s=999)
    out = await _call(server, "aegis_monitor", from_handle="p",
                      description="mine", done="never", interval_s=999)
    assert "also_watching" not in out


async def test_aegis_monitors_can_scope_to_one_handle():
    """Unscoped, the list is every monitor of every peer — 12 rows in the
    session that motivated this, with nothing saying which were the agent's
    own. That is the call the roster tells the agent to make."""
    mm = _mm()
    server = build_server(_Bridge(mm))
    mine = await _call(server, "aegis_monitor", from_handle="p",
                       description="mine", done="never", interval_s=999)
    await _call(server, "aegis_monitor", from_handle="peer",
                description="theirs", done="never", interval_s=999)
    assert len(await _call(server, "aegis_monitors")) == 2
    scoped = await _call(server, "aegis_monitors", from_handle="p")
    assert [r["id"] for r in scoped] == [mine["monitor_id"]]


def test_briefing_and_priming_tell_the_agent_to_read_the_roster():
    """The roster only works if the agent knows to act on it — a list it
    scrolls past is the same as no list."""
    for text in (BRIEFING, PRIMING):
        assert "roster" in text.lower()
        assert "cancel" in text.lower()
