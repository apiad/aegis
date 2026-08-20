from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.widgets import ContentSwitcher

from aegis.tui.file_browser_tab import FileBrowserTab
from aegis.tui.file_index import FileIndexer
from aegis.tui.state import AgentState


def _make_tab(tmp_path: Path, *, prefill: str = "", sidebar_open: bool = False) -> FileBrowserTab:
    idx = FileIndexer()
    return FileBrowserTab(cwd=tmp_path, indexer=idx, prefill=prefill, sidebar_open=sidebar_open)


class _Host(App):
    def __init__(self, tab: FileBrowserTab) -> None:
        super().__init__()
        self._tab = tab

    def compose(self) -> ComposeResult:
        yield ContentSwitcher(id="cs")

    async def on_mount(self) -> None:
        cs = self.query_one("#cs", ContentSwitcher)
        cs.display = False
        await cs.mount(self._tab)
        cs.current = self._tab.id


def test_quacks_like_pane(tmp_path: Path):
    tab = _make_tab(tmp_path)
    assert isinstance(tab.handle, str)
    assert tab.handle.startswith("browser:")
    assert tab.agent_slug == "browser"
    assert tab.state is AgentState.ready
    assert tab.unseen is False
    assert tab.id is not None


def test_multiple_tabs_have_distinct_handles(tmp_path: Path):
    idx = FileIndexer()
    t1 = FileBrowserTab(cwd=tmp_path, indexer=idx)
    t2 = FileBrowserTab(cwd=tmp_path, indexer=idx)
    assert t1.handle != t2.handle
    assert t1.id != t2.id


@pytest.mark.asyncio
async def test_set_task_dock_hides_sidebar(tmp_path: Path):
    tab = _make_tab(tmp_path, sidebar_open=True)
    app = _Host(tab)
    async with app.run_test() as pilot:
        await pilot.pause()
        sidebar = tab.query_one("#fb-sidebar")
        assert sidebar.display is True
        tab.set_task_dock(False)
        await pilot.pause()
        assert sidebar.display is False


@pytest.mark.asyncio
async def test_set_task_dock_shows_sidebar(tmp_path: Path):
    tab = _make_tab(tmp_path, sidebar_open=False)
    app = _Host(tab)
    async with app.run_test() as pilot:
        await pilot.pause()
        sidebar = tab.query_one("#fb-sidebar")
        assert sidebar.display is False
        tab.set_task_dock(True)
        await pilot.pause()
        assert sidebar.display is True
