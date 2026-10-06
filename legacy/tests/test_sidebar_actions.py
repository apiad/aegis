"""The F3 column's stop / restart pair.

F10's pair acts on any card; this one acts only on the tab in front of you,
which is what makes it one click instead of open-the-fleet-then-find-it. The
assertions are on real clicks and on what reached the session, never on the
message alone: a button that posts a message nothing handles looks identical
to a working one from the widget's side.
"""
from __future__ import annotations

import asyncio

import pytest

from aegis.config import Agent
from aegis.events import Result
from aegis.tui.app import AegisApp
from aegis.tui.pane import RESTART_TEXT
from aegis.tui.sidebar import RESTART, STOP, Sidebar, SidebarActions, _Button
from aegis.tui.state import AgentState


def _agent():
    return Agent(harness="claude-code", model="opus",
                 effort="high", permission="auto")


class GatedSession:
    """Holds its turn open until released, so `working` is observable."""

    session_id = "sid-1"

    def __init__(self):
        self.sent: list[str] = []
        self.interrupted = False
        self._gate = asyncio.Event()

    async def start(self): pass

    async def send(self, text): self.sent.append(text)

    async def events(self):
        await self._gate.wait()
        yield Result(duration_ms=1, is_error=False)
        self._gate.clear()

    async def interrupt(self): self.interrupted = True

    async def close(self): pass


class FakeMCP:
    url = "http://127.0.0.1:0/mcp/"

    def bind(self, bridge): pass
    async def start(self): pass
    async def stop(self): pass


def _app(sess):
    def make(agent, mcp_url, handle, **kw):
        return sess
    return AegisApp({"default": _agent()}, "default", make, FakeMCP())


async def _open_column(app, pilot):
    pane = app._panes[0]
    pane.toggle_task_dock()
    await pilot.pause()
    buttons = {b.action_name: b for b in pane.query_one(Sidebar).query(_Button)}
    return pane, buttons


@pytest.mark.asyncio
async def test_the_column_carries_both_buttons_labelled():
    app = _app(GatedSession())
    async with app.run_test(size=(160, 40)) as pilot:
        _pane, buttons = await _open_column(app, pilot)
        assert set(buttons) == {"stop", "restart"}
        assert STOP in buttons["stop"].render().plain
        assert RESTART in buttons["restart"].render().plain


@pytest.mark.asyncio
async def test_the_pair_is_hidden_with_the_column():
    """It rides F3's mode: closed, the pane is what it was before.

    On real geometry, not on `.display`: the row's own display stays True
    while the column above it is hidden, so that assertion would pass against
    a pair sitting in the middle of the transcript.
    """
    app = _app(GatedSession())
    async with app.run_test(size=(160, 40)) as pilot:
        pane = app._panes[0]
        await pilot.pause()
        row = pane.query_one(SidebarActions)
        assert row.region.area == 0
        pane.toggle_task_dock()
        await pilot.pause()
        assert row.region.area > 0


@pytest.mark.asyncio
async def test_clicking_stop_cuts_this_panes_turn():
    sess = GatedSession()
    app = _app(sess)
    async with app.run_test(size=(160, 40)) as pilot:
        pane, buttons = await _open_column(app, pilot)
        await pane._core.send("go")
        await pilot.pause()
        assert pane.state is AgentState.working

        await pilot.click(buttons["stop"])
        await pilot.pause()
        assert sess.interrupted is True
        assert sess.sent == ["go"], "stop must not send anything"


@pytest.mark.asyncio
async def test_clicking_restart_cuts_the_turn_and_sends_continue():
    sess = GatedSession()
    app = _app(sess)
    async with app.run_test(size=(160, 40)) as pilot:
        pane, buttons = await _open_column(app, pilot)
        await pane._core.send("go")
        await pilot.pause()
        assert pane.state is AgentState.working

        await pilot.click(buttons["restart"])
        await pilot.pause()
        assert sess.interrupted is True
        assert sess.sent[-1] == RESTART_TEXT


@pytest.mark.asyncio
async def test_clicking_restart_on_an_idle_tab_just_sends_continue():
    sess = GatedSession()
    app = _app(sess)
    async with app.run_test(size=(160, 40)) as pilot:
        pane, buttons = await _open_column(app, pilot)
        assert pane.state is not AgentState.working

        await pilot.click(buttons["restart"])
        await pilot.pause()
        assert sess.interrupted is False
        assert sess.sent[-1] == RESTART_TEXT
