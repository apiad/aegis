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
    from textual.widgets import Static
    body = pane.query_one(Sidebar).query_one(Static)
    return "\n".join(body.render_line(y).text
                     for y in range(body.size.height))


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
async def test_the_open_sidebar_carries_todays_date():
    app = _app()
    async with app.run_test(size=(120, 40)) as pilot:
        pane = app._panes[0]
        pane.toggle_task_dock()
        await pilot.pause()
        assert datetime.now().strftime("%Y-%m-%d") in _painted(pane)
