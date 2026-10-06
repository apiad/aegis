"""Work a background tab must not do, and bells it must not ring.

Hidden ConversationPanes already freeze their cosmetic timers; terminal
tabs did not. And every finished turn rang the terminal bell, including
each of a fleet of queue workers — a stream of BELs (or, with a visual
bell configured, full-screen flashes) that reads as the UI being janky.
"""
from __future__ import annotations

import pytest

from aegis.config import Agent
from aegis.events import AssistantText, Result
from aegis.tui.app import AegisApp


def _agent():
    return Agent(harness="claude-code", model="opus", effort="high",
                 permission="auto")


class FakeSession:
    def __init__(self):
        self.sent = []
    async def start(self): pass
    async def send(self, text): self.sent.append(text)
    async def events(self):
        yield AssistantText("x")
        yield Result(duration_ms=1, is_error=False)
    async def close(self): pass


class FakeMCP:
    url = "http://127.0.0.1:0/mcp/"
    def bind(self, bridge): pass
    async def start(self): pass
    async def stop(self): pass


def _app():
    return AegisApp({"default": _agent()}, "default",
                    lambda *a, **kw: FakeSession(), FakeMCP())


@pytest.mark.asyncio
async def test_a_burst_of_finished_turns_rings_one_bell():
    """Ten workers finishing together is one notification, not ten."""
    from aegis.tui.pane import PaneStateChanged

    app = _app()
    async with app.run_test() as pilot:
        rung = []
        app.bell = lambda: rung.append(1)
        pane = app._panes[0]
        for _ in range(10):
            app.on_pane_state_changed(PaneStateChanged(pane, True))
        await pilot.pause()
        assert len(rung) == 1


@pytest.mark.asyncio
async def test_a_later_turn_rings_again():
    """The rate limit is a window, not a one-shot mute. (Winding the app's
    own last-bell stamp back, rather than patching time.monotonic — Textual
    drives its timer loop off that, and freezing it hangs the app.)"""
    from aegis.tui.pane import PaneStateChanged

    app = _app()
    async with app.run_test() as pilot:
        rung = []
        app.bell = lambda: rung.append(1)
        pane = app._panes[0]
        app.on_pane_state_changed(PaneStateChanged(pane, True))
        app._last_bell -= app.BELL_INTERVAL_S * 2      # window has passed
        app.on_pane_state_changed(PaneStateChanged(pane, True))
        await pilot.pause()
        assert len(rung) == 2


@pytest.mark.asyncio
async def test_roster_writes_are_coalesced():
    """The tab bar refreshes on every pane state change, and each write is
    a full atomic rewrite of workspace.json. A burst is one write."""
    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        writes = []
        app._write_snapshot = lambda: writes.append(1)
        for _ in range(20):
            app._refresh_tabbar()
        await pilot.pause()
        assert writes == []                  # nothing synchronous
        app._flush_snapshot()                # what the timer fires
        assert writes == [1]


@pytest.mark.asyncio
async def test_quit_still_writes_the_roster_synchronously():
    """A debounced write must not lose the roster on the way out."""
    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        writes = []
        app._write_snapshot = lambda: writes.append(1)
        await app.action_quit()
        assert writes == [1]


@pytest.mark.asyncio
async def test_hidden_terminal_tab_freezes_its_footer_timer():
    """Mirrors what ConversationPane.on_hide already does."""
    from aegis.tui.terminal_tab import TerminalTab

    app = _app()
    async with app.run_test() as pilot:
        tab = TerminalTab.__new__(TerminalTab)     # no PTY needed
        stopped = []
        restarted = []
        tab._stop_timer = lambda: stopped.append(1)
        tab._running_block = object()
        tab._restart_timer = lambda: restarted.append(1)

        TerminalTab.on_hide(tab)
        assert stopped == [1]
        TerminalTab.on_show(tab)
        assert restarted == [1]
        await pilot.pause()


@pytest.mark.asyncio
async def test_idle_hidden_terminal_tab_does_not_restart_a_timer():
    from aegis.tui.terminal_tab import TerminalTab

    tab = TerminalTab.__new__(TerminalTab)
    restarted = []
    tab._running_block = None
    tab._restart_timer = lambda: restarted.append(1)
    TerminalTab.on_show(tab)
    assert restarted == []
