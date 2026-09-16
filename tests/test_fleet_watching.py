"""Who is watching which session.

A fleet watcher authorises a paid mid-turn recap, about a cent a call, so a
watcher on a session nobody is looking at spends money nobody sees. Every
count here is read off the session's own ``_fleet_watchers`` list, the one
the paid gate reads, and never off a flag on the pane or the screen.
"""

from __future__ import annotations

import asyncio

from aegis.config import Agent
from aegis.config.roots import AegisRoots
from aegis.recap import Recap
from aegis.tui.app import AegisApp
from aegis.tui.fleet_screen import FleetScreen
from aegis.tui.pane import ConversationPane
from aegis.tui.sidebar import Sidebar
from aegis.views.registry import ViewRegistry

from tests.brain import make_brain
from tests.views.conftest import FakeMCP


class _Harness:
    session_id = None

    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...

    async def events(self):
        if False:
            yield


def _agent():
    return Agent(harness="claude-code", model="opus", effort="high", permission="auto")


def _bridged(tmp_path):
    roster = {"default": _agent()}
    brain = make_brain(
        roster,
        "default",
        make_session=lambda *a, **k: _Harness(),
        mcp=None,
        roots=AegisRoots.for_project(tmp_path),
    )
    return AegisApp(
        mcp=FakeMCP(),
        agents=roster,
        default_agent="default",
        make_session=lambda *a, **k: _Harness(),
        queues={},
        clean=True,
        drivers={},
        cwd=str(tmp_path),
        voice=None,
        bridge=brain,
    )


async def _tabs(app, pilot, n):
    await pilot.pause()
    while len(app._panes) < n:
        await app._spawn("default")
        await pilot.pause()
    for _ in range(20):
        await pilot.pause()
        if len(app._panes) == n:
            return
    raise AssertionError(f"expected {n} tabs, have {len(app._panes)}")


def _watchers(app) -> list[int]:
    return [len(p._core._fleet_watchers) for p in app._panes]


async def test_a_closed_sidebar_watches_nothing(tmp_path):
    app = _bridged(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await _tabs(app, pilot, 2)
        assert _watchers(app) == [0, 0]


async def test_f3_watches_the_active_session_only(tmp_path):
    app = _bridged(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await _tabs(app, pilot, 3)
        app._activate(1)
        await pilot.press("f3")
        await pilot.pause()
        assert app.sidebar_mode
        assert _watchers(app) == [0, 1, 0]
        # Idempotent: reconciling again must not stack a second callback,
        # which would take two removes to undo.
        app.set_sidebar_mode(True)
        app._activate(1)
        assert _watchers(app) == [0, 1, 0]


async def test_a_tab_switch_with_f3_open_moves_the_watch(tmp_path):
    app = _bridged(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await _tabs(app, pilot, 2)
        app._activate(0)
        await pilot.press("f3")
        await pilot.pause()
        assert _watchers(app) == [1, 0]
        app._activate(1)
        await pilot.pause()
        assert _watchers(app) == [0, 1]


async def test_a_tab_opened_with_f3_up_takes_the_watch(tmp_path):
    """A spawn brings its tab forward without going through ``_activate``,
    and the tab it hides must stop paying."""
    app = _bridged(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await _tabs(app, pilot, 1)
        await pilot.press("f3")
        await pilot.pause()
        assert _watchers(app) == [1]
        await _tabs(app, pilot, 2)
        assert app._active is app._panes[1]
        assert _watchers(app) == [0, 1]


async def test_closing_f3_drops_every_watch(tmp_path):
    app = _bridged(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await _tabs(app, pilot, 2)
        await pilot.press("f3")
        await pilot.pause()
        await pilot.press("f3")
        await pilot.pause()
        assert not app.sidebar_mode
        assert _watchers(app) == [0, 0]


async def test_f10_watches_every_session_while_open(tmp_path):
    app = _bridged(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await _tabs(app, pilot, 3)
        app._activate(0)
        await pilot.press("f3")
        await pilot.pause()
        await pilot.press("f10")
        await pilot.pause()
        assert isinstance(app.screen, FleetScreen)
        assert all(n >= 1 for n in _watchers(app)), _watchers(app)
        assert _watchers(app) == [2, 1, 1]
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, FleetScreen)
        assert _watchers(app) == [1, 0, 0]


async def test_a_delivered_recap_refreshes_the_now_line(tmp_path):
    app = _bridged(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await _tabs(app, pilot, 1)
        await pilot.press("f3")
        await pilot.pause()
        pane = app._panes[0]
        core = pane._core
        bar = pane.query_one("#sidebar", Sidebar)
        assert bar._model.now_line == ""
        (cb,) = core._fleet_watchers
        # Delivered the way the session delivers it: stored, then handed out.
        recap = Recap(doing="wiring the watcher", ok=True)
        core.fleet_recap = recap
        cb(core, recap)
        assert bar._model.now_line == "wiring the watcher"
        assert "now wiring the watcher" in bar.plain()


# --- the daemon shape: a view over a brain, detached ---


async def _until(cond, rounds=400):
    for _ in range(rounds):
        if cond():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition never held")


async def test_a_detached_view_leaves_no_fleet_watcher_on_the_brain(tmp_path):
    """The brain keeps its sessions after a client goes away. A watcher the
    view left behind would keep paying for those sessions with nobody
    looking until they close. Detached the way the daemon detaches, through
    ``ViewRegistry.close``, with both F3 and F10 up."""
    roots = AegisRoots.for_project(tmp_path)
    roster = {"default": _agent()}
    mgr = make_brain(
        roster,
        "default",
        make_session=lambda *a, **k: _Harness(),
        mcp=None,
        roots=roots,
    )
    reg = ViewRegistry(
        manager=mgr,
        roots=roots,
        mcp=FakeMCP(),
        agents=roster,
        default_agent="default",
        make_session=lambda *a, **k: _Harness(),
    )
    view = await reg.open("tty-1", (120, 40))
    await view.run()
    app = view.app
    try:
        await mgr.spawn("default")
        await mgr.spawn("default")
        await _until(
            lambda: len([p for p in app._panes if isinstance(p, ConversationPane)])
            >= 2
        )
        sessions = list(mgr._sessions)
        # Posted to the app, the way a key reaches it: pushing a screen
        # needs the app's own context, which this test task does not have.
        app.call_next(app.set_sidebar_mode, True)
        app.call_next(app.action_open_fleet)
        await _until(lambda: isinstance(app.screen, FleetScreen))
        await _until(lambda: len(app.screen._hooked) == len(sessions))
        # F10 on every session, F3 on the active one: the detach below has
        # both kinds of watcher to leave behind.
        assert sorted(len(s._fleet_watchers) for s in sessions) == [1] * (
            len(sessions) - 1
        ) + [2]
    finally:
        await reg.close("tty-1")
    assert [len(s._fleet_watchers) for s in sessions] == [0] * len(sessions)
    assert all(s._fleet_task is None for s in sessions), (
        "a watcher is gone but the paid check still runs"
    )


async def test_a_view_whose_shutdown_hangs_still_releases_its_watchers(tmp_path, monkeypatch):
    """`View.stop` waits a bounded time for the app to exit and then cancels
    it. Textual runs the shutdown under a shield, so a hang inside it means
    no `on_unmount` ever runs — the pane and fleet-screen removals never
    happen, and the brain keeps paying for a client that is gone. Reproduced
    by the Task 14 review: watchers [2, 1] before and after. The view must
    release what its own widgets registered, whatever the shutdown does."""
    from aegis.views import view as view_mod

    monkeypatch.setattr(view_mod.View, "STOP_TIMEOUT_S", 0.5)
    roots = AegisRoots.for_project(tmp_path)
    roster = {"default": _agent()}
    mgr = make_brain(roster, "default", make_session=lambda *a, **k: _Harness(),
                     mcp=None, roots=roots)
    reg = ViewRegistry(manager=mgr, roots=roots, mcp=FakeMCP(), agents=roster,
                       default_agent="default", make_session=lambda *a, **k: _Harness())
    view = await reg.open("tty-1", (120, 40))
    await view.run()
    app = view.app
    await mgr.spawn("default")
    await mgr.spawn("default")
    await _until(lambda: len([p for p in app._panes if isinstance(p, ConversationPane)]) >= 2)
    sessions = list(mgr._sessions)
    app.call_next(app.set_sidebar_mode, True)
    app.call_next(app.action_open_fleet)
    await _until(lambda: isinstance(app.screen, FleetScreen))
    await _until(lambda: len(app.screen._hooked) == len(sessions))
    assert sum(len(s._fleet_watchers) for s in sessions) > 0

    async def hang(*a, **k):
        await asyncio.Event().wait()

    monkeypatch.setattr(app, "_close_all", hang)
    await reg.close("tty-1")
    assert [len(s._fleet_watchers) for s in sessions] == [0] * len(sessions)
    assert all(s._fleet_task is None for s in sessions), (
        "the view is gone but the paid check still runs"
    )


async def test_a_session_closed_while_f10_is_open_is_released(tmp_path):
    """`aegis dash` stays open all day. A screen that never lets go of a
    closed session holds it, and its observers, for as long as it is up."""
    app = _bridged(tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await _tabs(app, pilot, 2)
        await pilot.press("f10")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, FleetScreen)
        screen.refresh_fleet()
        gone = app._panes[1]._core
        assert any(gone is s for s in screen._hooked)
        await app.manager.close(gone.handle)
        for _ in range(20):
            await pilot.pause()
            if gone not in app.manager._sessions:
                break
        assert gone not in app.manager._sessions
        screen.refresh_fleet()
        assert not any(gone is s for s in screen._hooked)
        assert screen._on_event not in gone._extra_event_observers
        assert len(screen._hooked) == 1
