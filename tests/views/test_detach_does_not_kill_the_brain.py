"""Ctrl+Q in a daemon view detaches. The brain outlives it.

action_quit closes every pane, and a bridged app's panes wrap the BRAIN's
sessions — so the unguarded path kills the agents of every other view too.
This asserts on the substrate (the manager still holds its session, the
harness was never closed), not on the app's exit code.
"""
from aegis.config import Agent
from aegis.config.roots import AegisRoots
from aegis.core.manager import SessionManager
from aegis.views.registry import ViewRegistry

from tests.views.conftest import FakeMCP


class _FakeHarness:
    def __init__(self):
        self.closed = False

    async def start(self): ...
    async def send(self, t): ...

    async def close(self):
        self.closed = True

    async def events(self):
        if False:
            yield


def _reg(tmp_path):
    roots = AegisRoots.for_project(tmp_path)
    roster = {"default": Agent(harness="claude-code", model="opus",
                               effort="high", permission="auto")}
    harnesses = []

    def _make(prompt, agent, host):
        h = _FakeHarness()
        harnesses.append(h)
        return h

    mgr = SessionManager(roster, "default", make_session=_make,
                         mcp=None, roots=roots)
    reg = ViewRegistry(manager=mgr, roots=roots, mcp=FakeMCP(),
                       agents=roster, default_agent="default",
                       make_session=_make)
    return reg, mgr, harnesses


async def _mounted_panes(pilot, app, *, rounds=60):
    """Pump until the brain's sessions have become panes in this view.

    Not optional. A pane takes ~10 pumps to mount, and action_quit's
    teardown loop is over ``self._panes`` — so quitting after a single
    pause finds an empty list and the assertions below pass against the
    unguarded code. Measured: one pause -> 0 panes, ten -> 1.
    """
    from aegis.tui.pane import ConversationPane
    for _ in range(rounds):
        panes = [p for p in app._panes if isinstance(p, ConversationPane)]
        if panes:
            return panes
        await pilot.pause()
    raise AssertionError("no ConversationPane ever mounted in this view")


async def test_quitting_a_view_does_not_close_the_brains_session(tmp_path):
    """The defect, stated exactly: action_quit's teardown loop runs
    ``await pane.close()``, and a bridged pane's ``_core`` IS the brain's
    AgentSession (c511bc0). So the unguarded path closes the session that
    every OTHER view is looking at.

    Spied on the brain's own session object rather than on the fake
    harness: AgentSession.close only forwards to the harness when the
    session started, so a harness-level assertion is green whether or not
    close was called.
    """
    reg, mgr, _ = _reg(tmp_path)
    await mgr.spawn("default")
    session = mgr._sessions[0]
    closes = []
    original = session.close

    async def _spy(*a, **kw):
        closes.append(a)
        return await original(*a, **kw)

    session.close = _spy

    view = await reg.open("v1", (80, 24))
    async with view.app.run_test(headless=False, size=(80, 24)) as pilot:
        panes = await _mounted_panes(pilot, view.app)
        assert panes[0]._core is session, (
            "this view's pane does not wrap the brain's session, so this "
            "test cannot observe the defect it exists for")
        await view.app.action_quit()
        await pilot.pause()
    assert closes == [], \
        "a view quitting closed the brain's session under the other views"
    assert mgr.list_sessions(), "the brain lost its session when a view quit"


async def test_quitting_a_view_leaves_the_brains_mcp_plane_up(tmp_path):
    """The MCP server is _serve's, shared by every view. One view exiting
    must not take the agent plane down under the others."""
    reg, mgr, _ = _reg(tmp_path)
    view = await reg.open("v1", (80, 24))
    mcp = view.app._mcp
    stopped = []

    async def _watched_stop():
        stopped.append(True)

    mcp.stop = _watched_stop
    async with view.app.run_test(headless=False, size=(80, 24)) as pilot:
        await pilot.pause()
        await view.app.action_quit()
        await pilot.pause()
    assert not stopped, "a view quitting stopped the shared MCP plane"


async def test_the_local_tui_still_owns_its_brain(tmp_path):
    """The guard is opt-out, not a behaviour change for every caller.
    LocalTuiAttachment and the bootstrap TUI still tear down on quit."""
    from aegis.tui.app import AegisApp
    app = AegisApp({}, "", None, FakeMCP())
    assert app._owns_brain is True
