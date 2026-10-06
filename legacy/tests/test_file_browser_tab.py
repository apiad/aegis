from __future__ import annotations

import re
from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.widgets import (ContentSwitcher, DirectoryTree, Input, OptionList,
                             TextArea)
from textual.widgets.option_list import Option

from aegis.tui.file_browser_tab import _LIST_CAP, FileBrowserTab
from aegis.tui.file_index import FileIndexer
from aegis.tui.file_tab import FileTab
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


# ---------- escape --------------------------------------------------------
#
# The app binds escape at priority, so a focused widget's ``key_escape``
# never runs inside AegisApp (probed: the binding fires, the widget does
# not). Escape therefore arrives through ``action_interrupt``'s duck-typed
# ladder, and the tab's rung is ``escape_handled``.


@pytest.mark.asyncio
async def test_escape_returns_to_browse_from_view(tmp_path: Path):
    f = tmp_path / "esc.py"
    f.write_text("x = 1")
    idx = FileIndexer()
    idx.start(tmp_path)
    assert idx._ready.wait(5.0)

    tab = FileBrowserTab(cwd=tmp_path, indexer=idx)
    app = _Host(tab)
    async with app.run_test() as pilot:
        await pilot.pause()
        await tab._switch_to_view(f)
        await pilot.pause()
        # The editor holds focus, exactly as focus_input() leaves it.
        tab.query_one(TextArea).focus()
        await pilot.pause()
        assert tab.escape_handled() is True
        await pilot.pause()
        assert tab.query_one("#fb-browse").display is True
        assert tab.query_one("#fb-view").display is False
    idx.stop()


@pytest.mark.asyncio
async def test_escape_is_declined_in_browse_mode(tmp_path: Path):
    """Browse mode has nothing to go back to, so the rung must pass the
    key down the ladder rather than swallowing it."""
    idx = FileIndexer()
    tab = FileBrowserTab(cwd=tmp_path, indexer=idx)
    app = _Host(tab)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert tab.escape_handled() is False
    idx.stop()


@pytest.mark.asyncio
async def test_escape_reaches_the_editor_before_it_reaches_browse(tmp_path: Path):
    """An editing FileTab owns escape — leaving edit mode must not also
    throw away the file you were editing."""
    f = tmp_path / "editing.py"
    f.write_text("x = 1")
    idx = FileIndexer()
    tab = FileBrowserTab(cwd=tmp_path, indexer=idx)
    app = _Host(tab)
    async with app.run_test() as pilot:
        await pilot.pause()
        await tab._switch_to_view(f)
        await pilot.pause()
        ft = tab.query_one(FileTab)
        ft.key_e()                       # enter edit mode
        await pilot.pause()
        assert ft._edit_mode is True

        assert tab.escape_handled() is True
        await pilot.pause()
        assert ft._edit_mode is False, "escape did not leave edit mode"
        assert tab.query_one("#fb-view").display is True, \
            "escape fell through to browse and abandoned the open file"
    idx.stop()


@pytest.mark.asyncio
async def test_b_reaches_the_tab_with_the_editor_focused(tmp_path: Path):
    """A read-only TextArea does not swallow printable keys, so `b` still
    works after focus_input() parks focus on the editor. Pinned because
    the original `b` test pressed the key with focus elsewhere."""
    f = tmp_path / "focused.py"
    f.write_text("x = 1")
    idx = FileIndexer()
    tab = FileBrowserTab(cwd=tmp_path, indexer=idx)
    app = _Host(tab)
    async with app.run_test() as pilot:
        await pilot.pause()
        await tab._switch_to_view(f)
        await pilot.pause()
        tab.focus_input()
        await pilot.pause()
        await pilot.press("b")
        await pilot.pause()
        assert tab.query_one("#fb-browse").display is True
    idx.stop()


# ---------- the list ------------------------------------------------------


def _labels(ol: OptionList) -> list[str]:
    return [str(ol.get_option_at_index(i).prompt) for i in range(ol.option_count)]


@pytest.mark.asyncio
async def test_returning_to_browse_highlights_the_file_that_was_open(tmp_path: Path):
    for name in ("one.py", "two.py", "three.py"):
        (tmp_path / name).write_text("x")
    idx = FileIndexer()
    idx.start(tmp_path)
    assert idx._ready.wait(5.0)

    tab = FileBrowserTab(cwd=tmp_path, indexer=idx)
    app = _Host(tab)
    async with app.run_test() as pilot:
        await pilot.pause()
        await tab._switch_to_view(tmp_path / "two.py")
        await pilot.pause()
        tab.escape_handled()
        await pilot.pause()
        ol = tab.query_one("#fb-list", OptionList)
        assert ol.highlighted is not None
        assert ol.get_option_at_index(ol.highlighted).id == "two.py"
    idx.stop()


@pytest.mark.asyncio
async def test_refresh_keeps_the_highlight_on_the_same_file(tmp_path: Path):
    """The 2s poll rebuilds the list; it must not walk the cursor back to
    the top under someone who is arrowing through it."""
    for name in ("aaa.py", "bbb.py", "ccc.py"):
        (tmp_path / name).write_text("x")
    idx = FileIndexer()
    idx.start(tmp_path)
    assert idx._ready.wait(5.0)

    tab = FileBrowserTab(cwd=tmp_path, indexer=idx)
    app = _Host(tab)
    async with app.run_test() as pilot:
        await pilot.pause()
        ol = tab.query_one("#fb-list", OptionList)
        assert ol.option_count >= 3
        target = ol.get_option_at_index(2).id
        ol.highlighted = 2
        tab._refresh_list()
        await pilot.pause()
        assert ol.get_option_at_index(ol.highlighted).id == target
    idx.stop()


@pytest.mark.asyncio
async def test_list_says_how_many_files_it_dropped(tmp_path: Path):
    """The list is capped. A silent cap reads as a complete listing."""
    for i in range(_LIST_CAP + 5):
        (tmp_path / f"f{i:04d}.py").write_text("x")
    idx = FileIndexer()
    idx.start(tmp_path)
    assert idx._ready.wait(10.0)

    tab = FileBrowserTab(cwd=tmp_path, indexer=idx)
    app = _Host(tab)
    async with app.run_test() as pilot:
        await pilot.pause()
        ol = tab.query_one("#fb-list", OptionList)
        assert ol.option_count == _LIST_CAP + 1, "cap plus one marker row"
        marker = ol.get_option_at_index(ol.option_count - 1)
        assert marker.disabled is True
        assert "5 more" in str(marker.prompt)
    idx.stop()


@pytest.mark.asyncio
async def test_refresh_is_paused_in_view_mode(tmp_path: Path):
    """The poll keeps firing while the list is hidden; rebuilding it there
    is wasted work and destroys the highlight we just restored."""
    f = tmp_path / "paused.py"
    f.write_text("x = 1")
    idx = FileIndexer()
    idx.start(tmp_path)
    assert idx._ready.wait(5.0)

    tab = FileBrowserTab(cwd=tmp_path, indexer=idx)
    app = _Host(tab)
    async with app.run_test() as pilot:
        await pilot.pause()
        await tab._switch_to_view(f)
        await pilot.pause()
        ol = tab.query_one("#fb-list", OptionList)
        ol.clear_options()
        ol.add_option(Option("sentinel", id="sentinel"))
        tab._refresh_list()
        await pilot.pause()
        assert _labels(ol) == ["sentinel"], "the hidden list was rebuilt"
    idx.stop()


@pytest.mark.asyncio
async def test_tree_file_selected_opens_view(tmp_path: Path):
    f = tmp_path / "treefile.py"
    f.write_text("z = 3")
    idx = FileIndexer()
    tab = FileBrowserTab(cwd=tmp_path, indexer=idx, sidebar_open=True)
    app = _Host(tab)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Simulate a FileSelected message from the DirectoryTree
        tab.post_message(DirectoryTree.FileSelected(
            tab.query_one("#fb-tree", DirectoryTree),
            f,
        ))
        await pilot.pause()
        assert tab._current_file == f.resolve()
        assert tab.query_one("#fb-view").display is True
    idx.stop()


class _VisibleHost(App):
    """Host that actually lays the tab out, so regions are measurable."""

    def __init__(self, tab: FileBrowserTab) -> None:
        super().__init__()
        self._tab = tab

    def compose(self) -> ComposeResult:
        yield self._tab


@pytest.mark.asyncio
async def test_filter_input_renders_its_text(tmp_path: Path):
    """The filter input must have room to draw: a `height: 1` Input that keeps
    Textual's default `border: tall` collapses its content region to 0 rows —
    typing still filters, but the typed text is invisible."""
    idx = FileIndexer()
    tab = FileBrowserTab(cwd=tmp_path, indexer=idx)
    app = _VisibleHost(tab)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        inp = app.query_one("#fb-filter", Input)
        inp.focus()
        await pilot.pause()
        await pilot.press("a", "b", "c")
        await pilot.pause()
        assert inp.value == "abc"
        assert inp.content_region.height >= 1, "no room to draw the text"
        rendered = "".join(re.findall(r">([^<]*)<", app.export_screenshot()))
        assert "abc" in rendered
    idx.stop()
