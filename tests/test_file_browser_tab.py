from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.widgets import ContentSwitcher, Input, OptionList

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


import time as _time


@pytest.mark.asyncio
async def test_filter_narrows_list(tmp_path: Path):
    (tmp_path / "alpha.py").write_text("x")
    (tmp_path / "beta.py").write_text("y")
    idx = FileIndexer()
    idx.start(tmp_path)

    def _wait_ready():
        import threading
        assert idx._ready.wait(5.0)
    _wait_ready()

    tab = FileBrowserTab(cwd=tmp_path, indexer=idx)
    app = _Host(tab)
    async with app.run_test() as pilot:
        await pilot.pause()
        filt = tab.query_one("#fb-filter", Input)
        filt.value = "alpha"
        await pilot.pause()
        ol = tab.query_one("#fb-list", OptionList)
        labels = [ol.get_option_at_index(i).prompt for i in range(ol.option_count)]
        assert any("alpha.py" in lbl for lbl in labels)
        assert not any("beta.py" in lbl for lbl in labels)
    idx.stop()


@pytest.mark.asyncio
async def test_selecting_file_switches_to_view(tmp_path: Path):
    f = tmp_path / "target.py"
    f.write_text("print('hi')")
    idx = FileIndexer()
    idx.start(tmp_path)
    assert idx._ready.wait(5.0)

    tab = FileBrowserTab(cwd=tmp_path, indexer=idx)
    app = _Host(tab)
    async with app.run_test() as pilot:
        await pilot.pause()
        ol = tab.query_one("#fb-list", OptionList)
        # Get the first option and manually post the select event
        if ol.option_count > 0:
            opt = ol.get_option_at_index(0)
            ol.post_message(OptionList.OptionSelected(ol, opt, 0))
        await pilot.pause()
        view = tab.query_one("#fb-view")
        browse = tab.query_one("#fb-browse")
        # Check that classes are set correctly
        assert "active" in view.classes
        assert "hidden" in browse.classes
        assert tab._current_file is not None
    idx.stop()


@pytest.mark.asyncio
async def test_b_key_returns_to_browse(tmp_path: Path):
    f = tmp_path / "back.py"
    f.write_text("x = 1")
    idx = FileIndexer()
    idx.start(tmp_path)
    assert idx._ready.wait(5.0)

    tab = FileBrowserTab(cwd=tmp_path, indexer=idx)
    app = _Host(tab)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Switch to view first
        await tab._switch_to_view(f)
        await pilot.pause()
        assert tab.query_one("#fb-view").display is True
        # Press b
        await pilot.press("b")
        await pilot.pause()
        assert tab.query_one("#fb-browse").display is True
        assert tab.query_one("#fb-view").display is False
    idx.stop()


@pytest.mark.asyncio
async def test_prefill_existing_file_opens_view(tmp_path: Path):
    f = tmp_path / "preopen.py"
    f.write_text("y = 2")
    idx = FileIndexer()
    # Don't start indexer — prefill by path, not from index
    tab = FileBrowserTab(cwd=tmp_path, indexer=idx, prefill=str(f))
    app = _Host(tab)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()   # second pause for mount + open
        assert tab._current_file == f.resolve()
        assert tab.query_one("#fb-view").display is True
    idx.stop()


@pytest.mark.asyncio
async def test_prefill_nonexistent_populates_filter(tmp_path: Path):
    idx = FileIndexer()
    tab = FileBrowserTab(cwd=tmp_path, indexer=idx, prefill="myfile")
    app = _Host(tab)
    async with app.run_test() as pilot:
        await pilot.pause()
        filt = tab.query_one("#fb-filter", Input)
        assert filt.value == "myfile"
        assert tab._current_file is None
    idx.stop()
