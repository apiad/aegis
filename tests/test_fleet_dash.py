"""`aegis dash`, in the daemon: a view that comes up with the fleet on top.

The client asks in its hello and the daemon carries the request to the
view, because the app lives there and not in the client. Built the way the
daemon builds views, a `ViewRegistry` over one brain, with no socket."""

from __future__ import annotations

import asyncio

from aegis.config import Agent
from aegis.config.roots import AegisRoots
from aegis.tui.fleet_screen import FleetScreen
from aegis.tui.pane import ConversationPane
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


def _registry(tmp_path):
    roots = AegisRoots.for_project(tmp_path)
    roster = {"default": Agent(harness="claude-code", model="opus",
                               effort="high", permission="auto")}
    mgr = make_brain(roster, "default", make_session=lambda *a, **k: _Harness(),
                     mcp=None, roots=roots)
    reg = ViewRegistry(manager=mgr, roots=roots, mcp=FakeMCP(), agents=roster,
                       default_agent="default",
                       make_session=lambda *a, **k: _Harness())
    return reg, mgr


async def _until(cond, rounds=400):
    for _ in range(rounds):
        if cond():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition never held")


def _fleets(app) -> int:
    return sum(isinstance(s, FleetScreen) for s in app.screen_stack)


def _conversations(app) -> list:
    return [p for p in app._panes if isinstance(p, ConversationPane)]


async def _settle(app) -> None:
    """Let every message already posted to the app run."""
    done = asyncio.Event()
    app.call_next(done.set)
    await asyncio.wait_for(done.wait(), timeout=5)
    done.clear()
    app.call_next(done.set)
    await asyncio.wait_for(done.wait(), timeout=5)


async def test_a_view_opened_for_the_fleet_comes_up_showing_it(tmp_path):
    reg, _ = _registry(tmp_path)
    view = await reg.open("tty-dash", (120, 40), open="fleet")
    await view.run()
    try:
        await _until(lambda: isinstance(view.app.screen, FleetScreen))
    finally:
        await reg.close_all()


async def test_reopening_a_dash_view_never_stacks_or_closes_the_fleet(tmp_path):
    """Re-open returns the existing view. The request must be idempotent:
    a toggle (F10's own behaviour) would close a fleet already up."""
    reg, _ = _registry(tmp_path)
    view = await reg.open("tty-dash", (120, 40), open="fleet")
    await view.run()
    app = view.app
    try:
        await _until(lambda: isinstance(app.screen, FleetScreen))
        again = await reg.open("tty-dash", (120, 40), open="fleet")
        assert again is view
        await _settle(app)
        assert isinstance(app.screen, FleetScreen), "the re-open closed the fleet"
        assert _fleets(app) == 1
        # Twice before the app has a chance to run either request.
        await reg.open("tty-dash", (120, 40), open="fleet")
        await reg.open("tty-dash", (120, 40), open="fleet")
        await _settle(app)
        assert isinstance(app.screen, FleetScreen)
        assert _fleets(app) == 1
    finally:
        await reg.close_all()


async def test_reopening_a_plain_view_for_the_fleet_opens_it(tmp_path):
    reg, _ = _registry(tmp_path)
    view = await reg.open("tty-1", (120, 40))
    await view.run()
    app = view.app
    try:
        await _settle(app)
        assert _fleets(app) == 0
        # Twice before the app runs either: the check belongs to the app at
        # the moment the message runs, or the second request toggles.
        await reg.open("tty-1", (120, 40), open="fleet")
        await reg.open("tty-1", (120, 40), open="fleet")
        await _until(lambda: isinstance(app.screen, FleetScreen))
        await _settle(app)
        assert _fleets(app) == 1
    finally:
        await reg.close_all()


async def test_a_card_picked_in_the_dash_moves_only_that_views_tab(tmp_path):
    """Focus is per view (know-how/the-daemon.md). The dash on a second
    monitor switching tabs must not drag the operator's own terminal."""
    reg, mgr = _registry(tmp_path)
    main = await reg.open("tty-main", (120, 40))
    await main.run()
    dash = await reg.open("tty-dash", (120, 40))
    await dash.run()
    try:
        await mgr.spawn("default")
        await mgr.spawn("default")
        for v in (main, dash):
            await _until(lambda v=v: len(_conversations(v.app)) >= 2)
            v.app.call_next(v.app._activate, 0)
            await _until(lambda v=v: v.app._active is v.app._panes[0])

        await reg.open("tty-dash", (120, 40), open="fleet")
        await _until(lambda: isinstance(dash.app.screen, FleetScreen))
        screen = dash.app.screen
        await _until(lambda: len(screen._view().cards) >= 2)
        # Tab 2, as the `2` key picks it.
        dash.app.call_next(screen.action_pick, 2)

        await _until(lambda: not isinstance(dash.app.screen, FleetScreen))
        await _until(lambda: dash.app._active is dash.app._panes[1])
        await _settle(main.app)
        assert main.app._active is main.app._panes[0]
    finally:
        await reg.close_all()
