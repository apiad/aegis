"""bridge= must inject a local SessionManager WITHOUT degrading to the
--remote plane. The assertions map to the three ways the manager= path
fails: features nulled, hosts axis disabled, and RemoteSessionManager-only
methods (make_pane_core / _add_session / shutdown) called on a local
manager.
"""
from __future__ import annotations

from aegis.config import Agent
from aegis.config.roots import AegisRoots
from aegis.core.manager import SessionManager
from aegis.events import AssistantText, Result
from aegis.hosts.models import HostSpec
from aegis.tui.app import AegisApp
from aegis.tui.pane import ConversationPane


class FakeSession:
    def __init__(self):
        self.sent: list[str] = []
        self.started = self.closed = False
        self.session_id = None

    async def start(self): self.started = True
    async def send(self, text): self.sent.append(text)

    async def events(self):
        yield AssistantText("ok")
        yield Result(duration_ms=1, is_error=False)

    async def close(self): self.closed = True


class FakeMCP:
    """The local plane binds and starts the MCP server (app.py:478, :566)
    and stops it on quit (:1690); the --remote branch returns before any of
    that. `mcp=None` would therefore blow up in __init__ on the very path
    under test, so the stub is what lets a pass mean something."""

    url = "http://127.0.0.1:0/mcp/"
    port = 0

    def __init__(self):
        self.bound = None
        self.started = False
        self.stopped = False

    def bind(self, bridge): self.bound = bridge
    async def start(self): self.started = True
    async def stop(self): self.stopped = True


def _agent():
    return Agent(harness="claude-code", model="opus", effort="high",
                 permission="auto")


def _factory(*_a, **_k):
    return FakeSession()


def _manager(tmp_path):
    return SessionManager(
        agents={}, default_agent="", make_session=lambda *a, **k: None,
        mcp=None, roots=AegisRoots.for_project(tmp_path))


def _app(tmp_path, **kw):
    kw.setdefault("agents", {})
    kw.setdefault("default_agent", "")
    kw.setdefault("make_session", _factory)
    return AegisApp(mcp=FakeMCP(), queues={}, clean=True, drivers={},
                    cwd=str(tmp_path), voice=None,
                    bridge=_manager(tmp_path), **kw)


def _conversation_panes(app):
    return [p for p in app._panes if isinstance(p, ConversationPane)]


async def test_bridge_does_not_set_the_remote_sentinel(tmp_path):
    app = _app(tmp_path)
    async with app.run_test(size=(100, 30)):
        assert not hasattr(app, "_remote_manager"), (
            "bridge= must not take the --remote path; 9 hasattr guards "
            "switch local features off when that sentinel is present")


async def test_bridge_keeps_the_local_plane_on(tmp_path):
    mgr = _manager(tmp_path)
    app = AegisApp(agents={}, default_agent="", make_session=_factory,
                   mcp=FakeMCP(), queues={}, clean=True, drivers={},
                   cwd=str(tmp_path), voice=None, bridge=mgr)
    async with app.run_test(size=(100, 30)):
        # app.py:924 hands the pane a None queue_manager on the remote path,
        # and :389 replaces the plane itself with a _DisabledPlaneStub.
        assert app.queue_manager is not None
        assert type(app.queue_manager).__name__ == "QueueManager"
        assert type(app.terminal_manager).__name__ == "TerminalManager"
        assert app.manager is mgr


async def test_bridge_keeps_the_hosts_axis_on(tmp_path):
    """app.py:1324 — `self._hosts and not hasattr(self, "_remote_manager")`.
    With the sentinel set the host tier is skipped outright and every spawn
    is silently local."""
    from aegis.tui.picker import _ChoicePicker

    app = _app(tmp_path,
               hosts={"vps": HostSpec(name="vps", ssh="vps", cwd="/tmp")})
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("ctrl+n")
        await pilot.pause()
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, _ChoicePicker), (
            f"expected the host tier of the spawn picker, got {screen!r}")
        assert screen._title == "host"
        await pilot.press("escape")
        await pilot.pause()


def test_the_remote_only_methods_are_still_remote_only(tmp_path):
    """Why the two seams cannot be one. `make_pane_core` (app.py:1954),
    `_add_session` (:1247) and `shutdown` (:1671) are reached from inside
    the `_remote_manager` guards, and none of them exists on
    SessionManager — so routing bridge= through manager= would mount fine
    and then AttributeError on opening a pane and on quit.
    """
    from aegis.tui.remote_manager import RemoteSessionManager

    remote_only = ("make_pane_core", "_add_session", "shutdown")
    local = _manager(tmp_path)
    for name in remote_only:
        assert not hasattr(local, name), (
            f"SessionManager grew {name}; the two seams may have converged")
        assert hasattr(RemoteSessionManager, name)


async def test_bridge_survives_a_pane_and_a_clean_quit(tmp_path):
    """The failure mode a launch-and-look check misses: mount succeeds,
    then make_pane_core / _add_session / shutdown blow up, because those
    three exist only on RemoteSessionManager."""
    app = _app(tmp_path, agents={"main": _agent()}, default_agent="main")
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        # Boot spawns the default pane through the local _spawn, not through
        # _action_new_tab_remote -> _add_session -> make_pane_core.
        assert len(_conversation_panes(app)) == 1
        await pilot.press("ctrl+t")
        await pilot.pause()
        assert len(_conversation_panes(app)) == 2
        await pilot.press("ctrl+q")
        await pilot.pause()
    # The remote quit branch (:1670) calls _remote_manager.shutdown() and
    # returns before any of this. The unbind loop runs first and _mcp.stop()
    # last, so the two together prove the whole local teardown ran.
    assert app.inbox_router._sessions == {}
    assert app._mcp.stopped
