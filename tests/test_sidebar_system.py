"""The SYSTEM section, in a running app.

Assertions are on what the widget actually painted, never on the model
field being populated: a model assertion passes against a section that was
never composed into `SECTIONS`. Same discipline as `test_sidebar_repos.py`,
and the same `_painted` seam.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from aegis.config import Agent
from aegis.events import Result
from aegis.tui.app import AegisApp
from aegis.tui.sidebar import Sidebar
from aegis.version import BUILD


def _agent():
    return Agent(harness="claude-code", model="opus",
                 effort="high", permission="auto")


class FakeSession:
    session_id = "sid-1"

    def __init__(self):
        self.sent = []

    async def start(self): pass
    async def send(self, text): self.sent.append(text)

    async def events(self):
        yield Result(duration_ms=1, is_error=False)

    async def close(self): pass


class FakeMCP:
    url = "http://127.0.0.1:0/mcp/"

    def bind(self, bridge): self.bound = bridge
    async def start(self): pass
    async def stop(self): pass


def _app():
    def make(agent, mcp_url, handle, **kw):
        return FakeSession()
    return AegisApp({"default": _agent()}, "default", make, FakeMCP())


def _painted(pane) -> str:
    """What the sidebar put in its widget — every row of it.

    Read off the `Static`'s own renderable rather than off `SidebarModel`:
    a model assertion is green against a section that was never wired into
    `SECTIONS`, which is the bug this file exists to catch.

    Deliberately NOT `render_line` over `range(body.size.height)`. Geometry
    lags content by a layout pass, so under load one `pilot.pause()` leaves
    the widget measuring a row short, and the last row of the last section
    drops out of the assertion's view while sitting right there on screen.
    Reproduced by dropping the pause: content 10 rows, `size.height` 0,
    `BUILD` in the content and absent from the read. It cost one red suite
    whose failure would not reproduce alone.

    `_paints` carries the half that a re-render cannot: that the widget was
    actually updated, rather than the column merely being composable.
    """
    sidebar = pane.query_one(Sidebar)
    assert sidebar._paints > 0, "the sidebar never painted"
    return sidebar.plain()


@pytest.mark.asyncio
async def test_the_open_sidebar_answers_where_and_which_build():
    """The two questions a stale checkout makes you ask, on screen instead
    of in a shell: which directory this aegis is rooted at, and which build
    of it is running."""
    app = _app()
    async with app.run_test(size=(120, 40)) as pilot:
        pane = app._panes[0]
        pane.toggle_task_dock()
        await pilot.pause()

        painted = _painted(pane)
        assert "SYSTEM" in painted
        # Anchored on the row's own label, not on the directory name: this
        # checkout is called `aegis`, so a bare name check is green off the
        # build row alone and says nothing about the CWD row existing.
        cwd_row = next((ln for ln in painted.split("\n") if "CWD" in ln), "")
        assert Path.cwd().name in cwd_row
        assert BUILD in painted


@pytest.mark.asyncio
async def test_the_read_does_not_depend_on_the_layout_having_settled():
    """The seam these tests assert through must not lag the content.

    A layout pass is not a precondition of the column being composed, but
    `size.height` is a layout fact — so reading rows out of it silently
    hides whatever sits past the stale bound, worst at the bottom of the
    last section. This is the same open sidebar with the pause removed,
    which is what a loaded machine produces; the old read returned nothing
    at all here.
    """
    app = _app()
    async with app.run_test(size=(120, 40)) as pilot:
        pane = app._panes[0]
        pane.toggle_task_dock()
        assert BUILD in _painted(pane)


@pytest.mark.asyncio
async def test_the_open_sidebar_carries_todays_date():
    app = _app()
    async with app.run_test(size=(120, 40)) as pilot:
        pane = app._panes[0]
        pane.toggle_task_dock()
        await pilot.pause()
        assert datetime.now().strftime("%Y-%m-%d") in _painted(pane)
