import pytest
from pathlib import Path

from aegis.mcp.bridge import SessionInfo
from aegis.mcp.server import build_server
from aegis.terminal.manager import TerminalManager
from aegis.terminal.notify import make_terminal_notifier


class FakeBridge:
    queue_manager = None

    def __init__(self, tm):
        from aegis.queue import InboxRouter
        self.inbox_router = InboxRouter()
        self.canvas_manager = None
        self.terminal_manager = tm
        self._sessions: list[SessionInfo] = []

    def list_sessions(self):
        return list(self._sessions)

    def list_agents(self):
        return []

    async def handoff(self, a, b, c):
        return f"ignored: {a}->{b}"

    async def spawn(self, profile, *, handle=None):
        return handle or "stub"

    async def close(self, handle):
        return None


async def _call(server, _tool_name, **kwargs):
    tools = await server.list_tools()
    tool = next(t for t in tools if t.name == _tool_name)
    result = await tool.run(kwargs)
    sc = getattr(result, "structured_content", None)
    if isinstance(sc, dict) and "result" in sc:
        return sc["result"]
    if sc is not None:
        return sc
    return result.content[0].text


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    return tmp_path / "state"


async def test_term_spawn_and_list(state_dir):
    tm = TerminalManager(state_dir=state_dir)
    bridge = FakeBridge(tm)
    server = build_server(bridge)
    res = await _call(server, "aegis_term_spawn",
                      name="build", from_handle="agent:a")
    assert res["name"] == "build"
    assert res["pid"] > 0
    listed = await _call(server, "aegis_term_list")
    names = [t["name"] for t in listed]
    assert "build" in names
    await tm.close("build")


async def test_term_run_returns_record(state_dir):
    tm = TerminalManager(state_dir=state_dir)
    bridge = FakeBridge(tm)
    server = build_server(bridge)
    await _call(server, "aegis_term_spawn",
                name="t", from_handle="agent:a")
    res = await _call(server, "aegis_term_run",
                      name="t", cmd="true", from_handle="agent:a")
    assert res["exit"] == 0
    assert res["cmd"] == "true"
    await tm.close("t")


async def test_term_subscribe_unsubscribe(state_dir):
    tm = TerminalManager(state_dir=state_dir)
    bridge = FakeBridge(tm)
    server = build_server(bridge)
    await _call(server, "aegis_term_spawn", name="s", from_handle="agent:a")
    sub = await _call(server, "aegis_term_subscribe",
                      name="s", from_handle="alice")
    assert sub["ok"] is True
    assert "agent:alice" in sub["subscribers"]
    unsub = await _call(server, "aegis_term_unsubscribe",
                        name="s", from_handle="alice")
    assert unsub["ok"] is True
    await tm.close("s")


async def test_term_read_returns_records(state_dir):
    tm = TerminalManager(state_dir=state_dir)
    bridge = FakeBridge(tm)
    server = build_server(bridge)
    await _call(server, "aegis_term_spawn", name="r", from_handle="agent:a")
    await _call(server, "aegis_term_run",
                name="r", cmd="echo a", from_handle="agent:a")
    await _call(server, "aegis_term_run",
                name="r", cmd="echo b", from_handle="agent:a")
    recs = await _call(server, "aegis_term_read", name="r", last_n=2)
    assert len(recs) == 2
    assert recs[-1]["cmd"] == "echo b"
    await tm.close("r")


async def test_term_close_through_mcp(state_dir):
    tm = TerminalManager(state_dir=state_dir)
    bridge = FakeBridge(tm)
    server = build_server(bridge)
    await _call(server, "aegis_term_spawn", name="c", from_handle="agent:a")
    res = await _call(server, "aegis_term_close",
                      name="c", from_handle="agent:a")
    assert res["ok"] is True
    listed = await _call(server, "aegis_term_list")
    assert not any(t["name"] == "c" for t in listed)


async def test_term_spawn_duplicate_returns_error(state_dir):
    tm = TerminalManager(state_dir=state_dir)
    bridge = FakeBridge(tm)
    server = build_server(bridge)
    await _call(server, "aegis_term_spawn", name="d", from_handle="agent:a")
    res = await _call(server, "aegis_term_spawn", name="d", from_handle="agent:a")
    assert "error" in res
    await tm.close("d")
