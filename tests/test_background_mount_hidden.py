"""Panes mounted in the background must be mounted hidden.

Textual's ContentSwitcher only hides children at its own mount or on a
``current`` old→new transition (its own ``add_content`` sets
``display = False`` before mounting for exactly this reason). Any code path
that does a bare ``cs.mount(pane)`` without foregrounding the result leaves
the new pane ``display=True`` stacked on top of the active one — the
"duplicated window" symptom fixed for the resume path in 5552e3c and still
present on the spawn path.
"""
from __future__ import annotations

import pytest

from aegis.config import Agent
from aegis.events import AssistantText, Result
from aegis.tui.app import AegisApp


def _agent():
    return Agent(harness="claude-code", model="opus",
                 effort="high", permission="auto")


class FakeSession:
    def __init__(self):
        self.sent = []
        self.started = self.closed = False
        self.session_id = None

    async def start(self): self.started = True
    async def send(self, text): self.sent.append(text)
    async def events(self):
        yield AssistantText("ok")
        yield Result(duration_ms=1, is_error=False)
    async def close(self): self.closed = True


class FakeMCP:
    url = "http://127.0.0.1:0/mcp/"

    def bind(self, bridge): self.bound = bridge
    async def start(self): pass
    async def stop(self): pass


def _factory(agent, mcp_url, handle):
    return FakeSession()


@pytest.mark.asyncio
async def test_agent_spawned_pane_mounts_hidden(tmp_path, monkeypatch):
    """aegis_spawn (AppBridge.spawn → _SessionManagerAdapter) mounts a pane
    in the background; the active tab must stay the only visible one."""
    monkeypatch.chdir(tmp_path)
    app = AegisApp({"default": _agent()}, "default", _factory, FakeMCP(),
                   cwd=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        active = app._active
        assert active is not None

        await app.spawn("default", handle="worker")
        await pilot.pause()
        await pilot.pause()

        assert [p.handle for p in app._panes] == [active.handle, "worker"]
        visible = [p.handle for p in app._panes if p.display]
        assert visible == [active.handle]


@pytest.mark.asyncio
async def test_resumed_file_tab_does_not_steal_focus(tmp_path, monkeypatch):
    """_open_file_tab foregrounds by default; the resume path must not, or a
    restored file tab both stacks on and steals the active agent tab."""
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "notes.md"
    target.write_text("hello\n")

    app = AegisApp({"default": _agent()}, "default", _factory, FakeMCP(),
                   cwd=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        agent_pane = app._active
        assert agent_pane is not None

        await app._open_file_tab(target, foreground=False)
        await pilot.pause()

        assert len(app._panes) == 2
        assert app._active is agent_pane
        visible = [p for p in app._panes if p.display]
        assert visible == [agent_pane]
