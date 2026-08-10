"""The REPOS section, in a running app.

Assertions are on what the widget actually painted, never on the model
field being populated: a model assertion passes against a section that was
never composed into `SECTIONS`, which is precisely the bug that would ship
a sidebar with no REPOS in it.
"""
from __future__ import annotations

import shutil
import subprocess

import pytest

from aegis.config import Agent
from aegis.events import AssistantText, Result, ToolUse
from aegis.tui.app import AegisApp
from aegis.tui.sidebar import Sidebar

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git not on PATH")


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
        yield AssistantText("ok")
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


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "myrepo"
    root.mkdir()
    for args in (("init", "-q", "-b", "trunk"),
                 ("config", "user.email", "t@e.com"),
                 ("config", "user.name", "T")):
        subprocess.run(["git", *args], cwd=root, check=True,
                       capture_output=True)
    (root / "a.txt").write_text("x")
    subprocess.run(["git", "add", "a.txt"], cwd=root, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True,
                   capture_output=True)
    return root


def _painted(pane) -> str:
    """What the sidebar actually drew.

    Read off `render_line` — the compositor's own output — rather than off
    `SidebarModel`. A model assertion is green against a section that was
    never wired into `SECTIONS`, which is the one bug this file exists to
    catch.
    """
    from textual.widgets import Static
    body = pane.query_one(Sidebar).query_one(Static)
    return "\n".join(body.render_line(y).text
                     for y in range(body.size.height))


def _write(pane, path):
    pane._core._fire_event(
        ToolUse(name="Write", summary="", raw_input={"file_path": str(path)}))


@pytest.mark.asyncio
async def test_a_write_puts_its_repo_in_the_open_sidebar(repo):
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        _write(pane, repo / "b.py")
        pane.toggle_task_dock()
        await pilot.pause()

        painted = _painted(pane)
        assert "REPOS" in painted
        assert "myrepo" in painted


@pytest.mark.asyncio
async def test_the_section_is_absent_until_something_is_written():
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        pane.toggle_task_dock()
        await pilot.pause()
        assert "REPOS" not in _painted(pane)


@pytest.mark.asyncio
async def test_the_branch_and_dirty_count_reach_the_panel(repo):
    """The free path (.git/HEAD) gives the branch on the first frame; the
    probe fills in the count. Both must actually arrive."""
    (repo / "dirty.txt").write_text("uncommitted")
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        _write(pane, repo / "b.py")
        pane.toggle_task_dock()
        await pilot.pause()
        assert "trunk" in _painted(pane)

        await app.repo_tracker.refresh(force=True)
        pane._refresh_sidebar()
        await pilot.pause()
        assert "~1" in _painted(pane)


@pytest.mark.asyncio
async def test_a_read_puts_nothing_on_the_board(repo):
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        pane._core._fire_event(ToolUse(
            name="Read", summary="",
            raw_input={"file_path": str(repo / "a.txt")}))
        pane.toggle_task_dock()
        await pilot.pause()
        assert "REPOS" not in _painted(pane)


@pytest.mark.asyncio
async def test_the_row_is_marked_as_this_agents_own(repo):
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        _write(pane, repo / "b.py")
        pane.toggle_task_dock()
        await pilot.pause()
        row = next(ln for ln in _painted(pane).split("\n")
                   if "myrepo" in ln)
        assert row.lstrip().startswith("●")


@pytest.mark.asyncio
async def test_a_peers_write_shows_up_in_this_panes_sidebar(repo):
    """The section is app-wide — that is the whole point of the mark."""
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        app.repo_tracker.record("some-peer", repo / "peer.py")
        pane.toggle_task_dock()
        await pilot.pause()
        row = next(ln for ln in _painted(pane).split("\n")
                   if "myrepo" in ln)
        assert row.lstrip().startswith("·")


@pytest.mark.asyncio
async def test_closing_the_tab_takes_its_repos_off_the_board(repo):
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        _write(pane, repo / "b.py")
        await pilot.pause()
        assert app.repo_tracker.snapshot()
        await pane.remove()
        await pilot.pause()
        assert app.repo_tracker.snapshot() == []
