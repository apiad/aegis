from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.widgets import ContentSwitcher, TextArea

from textual.widgets import MarkdownViewer

from aegis.tui.file_tab import FileTab
from aegis.tui.state import AgentState


class _Host(App):
    def __init__(self, tab: FileTab) -> None:
        super().__init__()
        self._tab = tab

    def compose(self) -> ComposeResult:
        yield ContentSwitcher(id="cs")

    async def on_mount(self) -> None:
        cs = self.query_one("#cs", ContentSwitcher)
        await cs.mount(self._tab)
        cs.current = self._tab.id


@pytest.mark.asyncio
async def test_file_tab_loads_content(tmp_path: Path):
    f = tmp_path / "hello.py"
    f.write_text("print('hello')")
    tab = FileTab(f)
    app = _Host(tab)
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = tab.query_one(TextArea)
        assert "print" in editor.text
        assert editor.read_only is True


def test_file_tab_quacks_like_pane(tmp_path: Path):
    """FileTab must expose handle, agent_slug, state, unseen, id."""
    f = tmp_path / "x.py"
    f.write_text("")
    tab = FileTab(f)
    assert isinstance(tab.handle, str)
    assert tab.agent_slug == "file"
    assert tab.state is AgentState.ready
    assert tab.unseen is False
    assert tab.id is not None


def test_file_tab_deduplication(tmp_path: Path):
    """Two FileTabs for the same path get the same id."""
    f = tmp_path / "dup.py"
    f.write_text("x = 1")
    tab1 = FileTab(f)
    tab2 = FileTab(f)
    assert tab1.id == tab2.id


@pytest.mark.asyncio
async def test_file_tab_edit_mode_toggle(tmp_path: Path):
    f = tmp_path / "edit.py"
    f.write_text("x = 1")
    tab = FileTab(f)
    app = _Host(tab)
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = tab.query_one(TextArea)
        assert editor.read_only is True
        await pilot.press("e")
        await pilot.pause()
        assert editor.read_only is False


@pytest.mark.asyncio
async def test_file_tab_save(tmp_path: Path):
    f = tmp_path / "save_me.py"
    f.write_text("old content")
    tab = FileTab(f)
    app = _Host(tab)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        editor = tab.query_one(TextArea)
        editor.load_text("new content")
        await pilot.press("ctrl+s")
        await pilot.pause()
    assert f.read_text() == "new content"


@pytest.mark.asyncio
async def test_file_tab_escape_exits_edit(tmp_path: Path):
    f = tmp_path / "esc.py"
    f.write_text("x = 1")
    tab = FileTab(f)
    app = _Host(tab)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        assert tab.query_one(TextArea).read_only is False
        await pilot.press("escape")
        await pilot.pause()
        assert tab.query_one(TextArea).read_only is True


@pytest.mark.asyncio
async def test_file_tab_escape_when_modified_shows_cancel_prompt(
        tmp_path: Path):
    f = tmp_path / "dirty.py"
    f.write_text("x = 1")
    tab = FileTab(f)
    app = _Host(tab)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        editor = tab.query_one(TextArea)
        editor.load_text("x = 2")
        tab._modified = True  # short-circuit Changed event timing
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert tab._cancel_pending is True
        assert tab._edit_mode is True
        # editor is parked read-only while the confirm bar is up so the
        # bar's keystrokes don't get typed into the buffer.
        assert editor.read_only is True


@pytest.mark.asyncio
async def test_file_tab_cancel_prompt_d_discards(tmp_path: Path):
    f = tmp_path / "discard.py"
    f.write_text("original")
    tab = FileTab(f)
    app = _Host(tab)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        editor = tab.query_one(TextArea)
        editor.load_text("MUTATED")
        tab._modified = True
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        assert tab._cancel_pending is False
        assert tab._edit_mode is False
        assert tab._modified is False
        assert editor.read_only is True
        assert editor.text.rstrip("\n") == "original"


@pytest.mark.asyncio
async def test_file_tab_cancel_prompt_escape_keeps_editing(tmp_path: Path):
    f = tmp_path / "keep.py"
    f.write_text("original")
    tab = FileTab(f)
    app = _Host(tab)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        editor = tab.query_one(TextArea)
        editor.load_text("MUTATED")
        tab._modified = True
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert tab._cancel_pending is False
        assert tab._edit_mode is True
        assert editor.read_only is False
        assert "MUTATED" in editor.text


@pytest.mark.asyncio
async def test_markdown_preview_lazy_mounts_and_toggles(tmp_path: Path):
    f = tmp_path / "doc.md"
    f.write_text("# Hi\nbody")
    tab = FileTab(f)
    app = _Host(tab)
    async with app.run_test() as pilot:
        await pilot.pause()
        # No MarkdownViewer on initial open — lazy.
        assert tab._md_viewer is None
        assert tab._preview_mode is False

        # p → preview: viewer mounts, becomes visible, editor hidden.
        await pilot.press("p")
        await pilot.pause()
        assert tab._preview_mode is True
        viewer = tab.query_one(MarkdownViewer)
        assert viewer.has_class("visible")
        assert tab.query_one(TextArea).has_class("hidden")

        # p → back to VIEW: viewer still mounted but hidden.
        await pilot.press("p")
        await pilot.pause()
        assert tab._preview_mode is False
        assert not viewer.has_class("visible")
        assert not tab.query_one(TextArea).has_class("hidden")


@pytest.mark.asyncio
async def test_preview_disabled_for_non_markdown(tmp_path: Path):
    f = tmp_path / "hello.py"
    f.write_text("print('hi')")
    tab = FileTab(f)
    app = _Host(tab)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("p")
        await pilot.pause()
        assert tab._preview_mode is False
        assert tab._md_viewer is None


@pytest.mark.asyncio
async def test_ctrl_x_invokes_xdg_open_in_view_mode(
        tmp_path: Path, monkeypatch):
    f = tmp_path / "open_me.html"
    f.write_text("<h1>hi</h1>")
    tab = FileTab(f)
    app = _Host(tab)
    calls: list[list[str]] = []

    def fake_popen(argv, **_kw):
        calls.append(argv)

        class _P:
            pass
        return _P()
    monkeypatch.setattr("subprocess.Popen", fake_popen)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+x")
        await pilot.pause()
    assert calls == [["xdg-open", str(f.resolve())]]


@pytest.mark.asyncio
async def test_ctrl_x_invokes_xdg_open_in_preview_mode(
        tmp_path: Path, monkeypatch):
    f = tmp_path / "open_me.md"
    f.write_text("# hi")
    tab = FileTab(f)
    app = _Host(tab)
    calls: list[list[str]] = []

    def fake_popen(argv, **_kw):
        calls.append(argv)

        class _P:
            pass
        return _P()
    monkeypatch.setattr("subprocess.Popen", fake_popen)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("p")
        await pilot.pause()
        assert tab._preview_mode is True
        await pilot.press("ctrl+x")
        await pilot.pause()
    assert calls == [["xdg-open", str(f.resolve())]]


@pytest.mark.asyncio
async def test_ctrl_x_noop_in_edit_mode(tmp_path: Path, monkeypatch):
    f = tmp_path / "no_open.py"
    f.write_text("x = 1")
    tab = FileTab(f)
    app = _Host(tab)
    calls: list[list[str]] = []

    def fake_popen(argv, **_kw):
        calls.append(argv)

        class _P:
            pass
        return _P()
    monkeypatch.setattr("subprocess.Popen", fake_popen)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        assert tab._edit_mode is True
        await pilot.press("ctrl+x")
        await pilot.pause()
    assert calls == []
