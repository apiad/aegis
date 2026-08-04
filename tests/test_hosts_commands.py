from __future__ import annotations

import asyncio

import pytest

from aegis.config import Agent
from aegis.core.manager import SessionManager
from aegis.hosts.models import HostSpec, Place


class _StubSession:
    supports_idle_events = False

    def __init__(self, session_id=None):
        self._session_id = session_id
        self.closed = False

    @property
    def session_id(self):
        return self._session_id

    async def start(self):
        pass

    async def send(self, text):
        pass

    async def events(self):
        return
        yield

    async def close(self):
        self.closed = True


def _manager(tmp_path, session_id="sid-1"):
    built: list[dict] = []

    def make_session(profile, mcp_url, handle, fork_from=None, place=None,
                     resume_from=None):
        built.append({"place": place, "resume_from": resume_from,
                      "handle": handle})
        return _StubSession(session_id)

    mgr = SessionManager(
        agents={"main": Agent(harness="claude-code", model="opus")},
        default_agent="main",
        make_session=make_session,
        mcp=None,
        hosts={"vps": HostSpec(name="vps", ssh="vps.apiad.net", cwd="/w")},
        local_root=str(tmp_path))
    return mgr, built


def test_reconnect_resumes_on_the_same_place(tmp_path):
    mgr, built = _manager(tmp_path)
    h = asyncio.run(mgr.spawn("main", host="vps"))
    asyncio.run(mgr.reconnect(h))
    assert built[-1]["place"] == Place("vps", "/w")
    assert built[-1]["resume_from"] == "sid-1"


def test_reconnect_keeps_the_same_handle_and_session(tmp_path):
    mgr, built = _manager(tmp_path)
    h = asyncio.run(mgr.spawn("main", host="vps"))
    before = mgr.get(h)
    asyncio.run(mgr.reconnect(h))
    # Same AgentSession object: the handle, log id, inbox binding and
    # observers all survive. Only the process underneath is new.
    assert mgr.get(h) is before
    assert built[-1]["handle"] == h


def test_reconnect_closes_the_dead_harness(tmp_path):
    mgr, _ = _manager(tmp_path)
    h = asyncio.run(mgr.spawn("main", host="vps"))
    dead = mgr.get(h)._session
    asyncio.run(mgr.reconnect(h))
    assert dead.closed
    assert mgr.get(h)._session is not dead


def test_reconnect_refuses_without_a_session_id(tmp_path):
    mgr, _ = _manager(tmp_path, session_id=None)
    h = asyncio.run(mgr.spawn("main", host="vps"))
    with pytest.raises(ValueError, match="session id"):
        asyncio.run(mgr.reconnect(h))


def test_reconnect_refuses_on_a_local_session(tmp_path):
    mgr, _ = _manager(tmp_path)
    h = asyncio.run(mgr.spawn("main"))
    with pytest.raises(ValueError, match="local"):
        asyncio.run(mgr.reconnect(h))


def test_reconnect_lists_every_refusal_reason_at_once(tmp_path):
    mgr, _ = _manager(tmp_path, session_id=None)
    h = asyncio.run(mgr.spawn("main"))
    with pytest.raises(ValueError) as e:
        asyncio.run(mgr.reconnect(h))
    assert "local" in str(e.value) and "session id" in str(e.value)


# --- @host parsing --------------------------------------------------------


def test_plain_agent_has_no_host():
    from aegis.hosts.resolve import parse_at_host
    assert parse_at_host("claude-code") == ("claude-code", None, None)


def test_agent_at_host():
    from aegis.hosts.resolve import parse_at_host
    assert parse_at_host("claude-code@vps") == ("claude-code", "vps", None)


def test_agent_at_host_with_cwd():
    from aegis.hosts.resolve import parse_at_host
    assert parse_at_host("claude-code@vps:/srv/app") == (
        "claude-code", "vps", "/srv/app")


def test_cwd_may_contain_colons_after_the_first():
    from aegis.hosts.resolve import parse_at_host
    assert parse_at_host("main@vps:/srv/a:b") == ("main", "vps", "/srv/a:b")


def test_empty_host_is_treated_as_absent():
    from aegis.hosts.resolve import parse_at_host
    assert parse_at_host("main@") == ("main", None, None)


def test_empty_cwd_is_treated_as_absent():
    from aegis.hosts.resolve import parse_at_host
    assert parse_at_host("main@vps:") == ("main", "vps", None)


def test_spawn_command_passes_host_and_cwd(tmp_path):
    mgr, _ = _manager(tmp_path)
    h = asyncio.run(mgr.spawn("main", host="vps", cwd="/srv/app"))
    assert mgr.get(h).place == Place("vps", "/srv/app")


# --- /spawn <agent>@<host>[:<cwd>] ---------------------------------------


class _SpawnBridge:
    _agents: dict = {}
    _hosts = {"vps": HostSpec(name="vps", ssh="h", cwd="/w")}

    def __init__(self, raise_host_error: bool = False):
        self.calls: list[dict] = []
        self._raise = raise_host_error

    def list_agents(self):
        return ["main", "opus"]

    def list_sessions(self):
        return []

    async def spawn(self, profile, *, opening_prompt=None, spawned_by=None,
                    model=None, effort=None, host=None, cwd=None,
                    prompt=None, handle=None):
        if self._raise:
            from aegis.hosts.errors import HostError
            raise HostError("unknown host 'nowhere'; known: ['vps', 'local']")
        self.calls.append({"profile": profile, "host": host, "cwd": cwd,
                           "prompt": opening_prompt})
        return "new-agent"


def _ctx(bridge):
    from aegis.commands import CommandContext
    return CommandContext(bridge=bridge, handle="me")


def _completion_values(rows) -> list[str]:
    """Completer rows are either a bare value or a (value, detail) pair —
    both shapes are legal and the palette handles each."""
    return [r if isinstance(r, str) else r[0] for r in rows]


@pytest.mark.asyncio
async def test_spawn_plain_agent_stays_local():
    from aegis.commands import dispatch

    b = _SpawnBridge()
    res = await dispatch("/spawn main", _ctx(b))
    assert res.ok
    assert b.calls[-1] == {"profile": "main", "host": None, "cwd": None,
                           "prompt": None}


@pytest.mark.asyncio
async def test_spawn_at_host():
    from aegis.commands import dispatch

    b = _SpawnBridge()
    await dispatch("/spawn main@vps", _ctx(b))
    assert b.calls[-1]["profile"] == "main"
    assert b.calls[-1]["host"] == "vps"
    assert b.calls[-1]["cwd"] is None


@pytest.mark.asyncio
async def test_spawn_at_host_with_cwd_and_prompt():
    from aegis.commands import dispatch

    b = _SpawnBridge()
    await dispatch("/spawn main@vps:/srv/app go and look around", _ctx(b))
    assert b.calls[-1]["host"] == "vps"
    assert b.calls[-1]["cwd"] == "/srv/app"
    assert b.calls[-1]["prompt"] == "go and look around"


@pytest.mark.asyncio
async def test_spawn_validates_the_agent_not_the_whole_token():
    from aegis.commands import dispatch

    b = _SpawnBridge()
    res = await dispatch("/spawn nosuch@vps", _ctx(b))
    assert not res.ok
    assert "nosuch" in res.title
    assert not b.calls


@pytest.mark.asyncio
async def test_spawn_reports_an_unknown_host_legibly():
    from aegis.commands import dispatch

    b = _SpawnBridge(raise_host_error=True)
    res = await dispatch("/spawn main@nowhere", _ctx(b))
    assert not res.ok
    assert "nowhere" in res.title
    assert "known" in res.body


def test_the_palette_offers_agent_at_host_combinations():
    from aegis.commands.builtins.core import _agent_at_host_choices

    vals = _completion_values(_agent_at_host_choices(_SpawnBridge()))
    assert "main" in vals and "main@vps" in vals and "opus@vps" in vals


def test_the_palette_offers_no_host_entries_when_none_configured():
    from aegis.commands.builtins.core import _agent_at_host_choices

    class _NoHosts(_SpawnBridge):
        _hosts: dict = {}

    vals = _completion_values(_agent_at_host_choices(_NoHosts()))
    assert not any("@" in v for v in vals)


@pytest.mark.asyncio
async def test_aegis_spawn_forwards_host_and_cwd():
    from aegis.mcp.server import build_server
    from tests.test_mcp_server import FakeBridge, _call

    br = FakeBridge()
    seen: dict = {}

    async def _spawn(agent, *, handle=None, opening_prompt=None,
                     spawned_by=None, model=None, effort=None, prompt=None,
                     host=None, cwd=None):
        seen.update({"agent": agent, "host": host, "cwd": cwd})
        return "new-agent"

    br.spawn = _spawn
    out = await _call(build_server(br), "aegis_spawn", agent="main",
                      prompt="go", from_handle="me", host="vps",
                      cwd="/srv/app")
    assert out == {"handle": "new-agent"}
    assert seen == {"agent": "main", "host": "vps", "cwd": "/srv/app"}


@pytest.mark.asyncio
async def test_aegis_spawn_returns_an_error_for_an_unknown_host():
    from aegis.hosts.errors import HostError
    from aegis.mcp.server import build_server
    from tests.test_mcp_server import FakeBridge, _call

    br = FakeBridge()

    async def _spawn(agent, **kw):
        raise HostError("unknown host 'nowhere'; known: ['vps', 'local']")

    br.spawn = _spawn
    out = await _call(build_server(br), "aegis_spawn", agent="main",
                      prompt="go", from_handle="me", host="nowhere")
    assert "nowhere" in out["error"]


@pytest.mark.asyncio
async def test_aegis_list_sessions_reports_each_peers_host():
    from aegis.mcp.bridge import SessionInfo
    from aegis.mcp.server import build_server
    from tests.test_mcp_server import FakeBridge, _call

    br = FakeBridge()
    br.list_sessions = lambda: [
        SessionInfo(handle="here", agent_slug="main", state="ready",
                    active=True, unseen=False),
        SessionInfo(handle="there", agent_slug="main", state="ready",
                    active=False, unseen=False, host="vps"),
    ]
    rows = await _call(build_server(br), "aegis_list_sessions")
    assert {r["handle"]: r["host"] for r in rows} == {
        "here": "local", "there": "vps"}


def test_the_briefing_tells_agents_about_hosts():
    from aegis.mcp.server import BRIEFING

    assert "EXECUTION HOSTS" in BRIEFING
    assert "host=\"vps\"" in BRIEFING
    # The trap worth naming out loud.
    assert "NOT interchangeable" in BRIEFING


def test_every_appbridge_spawn_accepts_host_and_cwd():
    """`host`/`cwd` must reach EVERY AppBridge.spawn implementation.

    There are several — SessionManager, AegisApp, RemoteSessionManager,
    the adapter the TUI routes through, the Protocol itself — and a slash
    command or MCP call lands on a different one depending on the
    frontend. Updating some and not others is invisible to a test that
    only exercises one: /spawn opus@vps died with a TypeError on the TUI
    path while the SessionManager tests stayed green. This asserts the
    whole set at once.
    """
    import inspect

    from aegis.core.manager import SessionManager
    from aegis.mcp.bridge import AppBridge
    from aegis.tui.app import AegisApp, _SessionManagerAdapter
    from aegis.tui.remote_manager import RemoteSessionManager
    from aegis.workflow.engine import WorkflowEngine

    impls = [
        ("AppBridge (protocol)", AppBridge.spawn),
        ("SessionManager", SessionManager.spawn),
        ("AegisApp", AegisApp.spawn),
        ("RemoteSessionManager", RemoteSessionManager.spawn),
        ("_SessionManagerAdapter", _SessionManagerAdapter.spawn),
        ("WorkflowEngine", WorkflowEngine.spawn),
    ]
    missing = []
    for name, fn in impls:
        params = inspect.signature(fn).parameters
        for arg in ("host", "cwd"):
            if arg not in params:
                missing.append(f"{name}.spawn is missing {arg!r}")
    assert not missing, "\n".join(missing)


@pytest.mark.asyncio
async def test_the_tui_bridge_actually_forwards_host_and_cwd(monkeypatch):
    """Behavioural counterpart to the signature check above.

    `/spawn opus@vps` lands on AegisApp.spawn, which delegates to
    _SessionManagerAdapter. A signature test proves the kwarg is accepted;
    this proves it is passed on rather than accepted and dropped.
    """
    from aegis.tui import app as app_mod

    seen: dict = {}

    class _Adapter:
        def __init__(self, app):
            pass

        def spawn(self, profile, **kw):
            seen.update({"profile": profile, **kw})
            from types import SimpleNamespace
            return SimpleNamespace(handle="new-agent")

    monkeypatch.setattr(app_mod, "_SessionManagerAdapter", _Adapter)

    handle = await app_mod.AegisApp.spawn(
        object(), "opus", host="vps", cwd="/srv/app")
    assert handle == "new-agent"
    assert seen["profile"] == "opus"
    assert seen["host"] == "vps"
    assert seen["cwd"] == "/srv/app"


@pytest.mark.asyncio
async def test_the_tui_can_reconnect_a_dropped_remote_pane():
    """/reconnect must work in the TUI, not just via SessionManager — the
    TUI is where a dropped link is actually noticed."""
    from types import SimpleNamespace

    from aegis.tui.app import AegisApp

    adopted: list = []
    built: dict = {}

    class _Session:
        session_id = "sid-1"

        async def close(self):
            pass

    core = SimpleNamespace(
        place=Place("vps", "/w"), session_id="sid-1",
        agent=object(), _session=_Session(),
        adopt=adopted.append)
    pane = SimpleNamespace(handle="a-b", _core=core)

    def _make_session(agent, url, handle, **kw):
        built.update(kw)
        return "fresh-harness"

    app = AegisApp.__new__(AegisApp)
    app._panes = [pane]
    app._make_session = _make_session
    app._mcp = SimpleNamespace(url="http://127.0.0.1:1/mcp/")
    app._refresh_tabbar = lambda: None

    msg = await AegisApp.reconnect(app, "a-b")
    assert "vps" in msg
    assert built["place"] == Place("vps", "/w")
    assert built["resume_from"] == "sid-1"
    # Same AgentSession adopted the new harness — the pane is not rebuilt.
    assert adopted == ["fresh-harness"]


@pytest.mark.asyncio
async def test_the_tui_refuses_to_reconnect_a_local_pane():
    from types import SimpleNamespace

    from aegis.tui.app import AegisApp

    core = SimpleNamespace(place=Place("local", "/w"), session_id="sid-1")
    app = AegisApp.__new__(AegisApp)
    app._panes = [SimpleNamespace(handle="a-b", _core=core)]

    with pytest.raises(ValueError, match="local"):
        await AegisApp.reconnect(app, "a-b")


@pytest.mark.asyncio
async def test_remote_mode_refuses_host_placement_legibly():
    """In --remote mode the harness lives in the serve we attached to, so
    placing it from here is meaningless — but it must say so, not raise a
    TypeError."""
    from aegis.tui.remote_manager import (
        RemoteSessionManager, RemoteUnsupportedError,
    )

    mgr = RemoteSessionManager.__new__(RemoteSessionManager)
    with pytest.raises(RemoteUnsupportedError, match="host/cwd"):
        await mgr.spawn("opus", host="vps")


def test_session_info_reports_the_host(tmp_path):
    mgr, _ = _manager(tmp_path)
    asyncio.run(mgr.spawn("main", host="vps"))
    asyncio.run(mgr.spawn("main"))
    assert sorted(s.host for s in mgr.list_sessions()) == ["local", "vps"]
