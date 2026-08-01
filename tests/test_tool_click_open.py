"""Ctrl+click a Read/Write/Edit block and the file it names opens here."""
from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.events import Click

from aegis.render_shared import FileTarget
from aegis.tui.pane import CopyableBlock


def _click(widget, *, ctrl=False, meta=False):
    return Click(widget=widget, x=0, y=0, delta_x=0, delta_y=0, button=1,
                 shift=False, meta=meta, ctrl=ctrl)


class _BlockApp(App):
    """A lone tool block, with the app-level opener stubbed out."""

    def __init__(self, target: FileTarget | None) -> None:
        super().__init__()
        self._target = target
        self.opened: list[tuple[str, int | None]] = []
        self.toggled: list[str] = []
        self.notices: list[str] = []

    def compose(self) -> ComposeResult:
        yield CopyableBlock("read x.py", "read x.py",
                            tool_call_id="c1", file_target=self._target)

    async def _open_file_tab(self, path, *, line=None,
                             foreground=True) -> None:
        self.opened.append((str(path), line))

    def on_copyable_block_tool_expand_toggle(self, event) -> None:
        self.toggled.append(event.tool_call_id)

    def notify(self, message, *a, **kw):                  # noqa: D102
        self.notices.append(str(message))


async def _fire(app, **mods):
    async with app.run_test() as pilot:
        block = app.query_one(CopyableBlock)
        block.on_click(_click(block, **mods))
        await pilot.pause()
        await pilot.pause()


@pytest.mark.asyncio
async def test_ctrl_click_on_a_read_opens_the_file(tmp_path: Path):
    f = tmp_path / "x.py"
    f.write_text("a = 1\n")
    app = _BlockApp(FileTarget(str(f)))
    await _fire(app, ctrl=True)
    assert app.opened == [(str(f), None)]
    assert app.toggled == []


@pytest.mark.asyncio
async def test_ctrl_click_on_a_read_with_an_offset_lands_on_that_line(
        tmp_path: Path):
    f = tmp_path / "x.py"
    f.write_text("\n".join(f"line {i}" for i in range(1, 40)))
    app = _BlockApp(FileTarget(str(f), line=31))
    await _fire(app, ctrl=True)
    assert app.opened == [(str(f), 31)]


@pytest.mark.asyncio
async def test_ctrl_click_on_an_edit_lands_where_the_edit_began(
        tmp_path: Path):
    f = tmp_path / "mod.py"
    f.write_text("import os\n\n\ndef foo():\n    return 1\n")
    app = _BlockApp(FileTarget(str(f), anchor="def foo():\n    return 1"))
    await _fire(app, ctrl=True)
    assert app.opened == [(str(f), 4)]


@pytest.mark.asyncio
async def test_a_relative_tool_path_resolves_against_cwd(
        tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "rel.py").write_text("x\n")
    app = _BlockApp(FileTarget("rel.py"))
    await _fire(app, ctrl=True)
    assert app.opened == [(str(tmp_path / "rel.py"), None)]


@pytest.mark.asyncio
async def test_an_edit_whose_anchor_is_gone_opens_at_the_top(tmp_path: Path):
    f = tmp_path / "moved.py"
    f.write_text("nothing like the anchor here\n")
    app = _BlockApp(FileTarget(str(f), anchor="def foo():"))
    await _fire(app, ctrl=True)
    assert app.opened == [(str(f), None)]


@pytest.mark.asyncio
async def test_plain_click_still_expands_the_args(tmp_path: Path):
    f = tmp_path / "x.py"
    f.write_text("a = 1\n")
    app = _BlockApp(FileTarget(str(f)))
    await _fire(app)
    assert app.opened == []
    assert app.toggled == ["c1"]


@pytest.mark.asyncio
async def test_ctrl_click_on_a_fileless_tool_falls_back_to_expanding():
    app = _BlockApp(None)
    await _fire(app, ctrl=True)
    assert app.opened == []
    assert app.toggled == ["c1"]


@pytest.mark.asyncio
async def test_a_vanished_file_says_so_instead_of_opening(tmp_path: Path):
    app = _BlockApp(FileTarget(str(tmp_path / "ghost.py")))
    await _fire(app, ctrl=True)
    assert app.opened == []
    assert any("ghost.py" in n for n in app.notices)


# --------------------------------------------------------------------------
# End to end, through the real app: a streamed Edit → a FileTab on its line
# --------------------------------------------------------------------------
from aegis.config import Agent                              # noqa: E402
from aegis.events import ToolUse                            # noqa: E402
from aegis.tui.app import AegisApp                          # noqa: E402
from aegis.tui.file_tab import FileTab                      # noqa: E402
from textual.widgets import TextArea                        # noqa: E402


class _FakeSession:
    async def start(self): pass
    async def send(self, t): pass
    async def events(self):
        if False:
            yield
    async def close(self): pass


class _FakeMCP:
    url = "http://127.0.0.1:0/mcp/"
    def bind(self, b): pass
    async def start(self): pass
    async def stop(self): pass


def _app():
    agent = Agent(harness="claude-code", model="opus", effort="high",
                  permission="auto")
    return AegisApp({"default": agent}, "default",
                    lambda a, u, h: _FakeSession(), _FakeMCP())


@pytest.mark.asyncio
async def test_ctrl_click_on_a_streamed_edit_opens_a_file_tab(tmp_path: Path):
    f = tmp_path / "mod.py"
    f.write_text("import os\n\n\ndef foo():\n    return 1\n")
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        pane._on_core_event(None, ToolUse(
            name="Edit", summary="edit mod.py", kind="edit",
            tool_call_id="c1",
            raw_input={"file_path": str(f), "old_string": "def foo():",
                       "new_string": "def bar():"},
            locations=((str(f), None),)))
        await pilot.pause()
        block = pane._mounted_blocks[-1]
        assert block._file_target is not None

        block.on_click(_click(block, ctrl=True))
        for _ in range(4):
            await pilot.pause()

        tabs = [p for p in app._panes if isinstance(p, FileTab)]
        assert len(tabs) == 1, "ctrl+click should open exactly one FileTab"
        editor = tabs[0].query_one(TextArea)
        assert editor.cursor_location == (3, 0)   # `def foo():` is line 4


@pytest.mark.asyncio
async def test_plain_click_on_a_streamed_read_opens_nothing(tmp_path: Path):
    f = tmp_path / "read.py"
    f.write_text("a = 1\n")
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        pane._on_core_event(None, ToolUse(
            name="Read", summary="read read.py", kind="read",
            tool_call_id="c1", raw_input={"file_path": str(f)},
            locations=((str(f), None),)))
        await pilot.pause()
        block = pane._mounted_blocks[-1]
        block.on_click(_click(block))
        for _ in range(4):
            await pilot.pause()
        assert [p for p in app._panes if isinstance(p, FileTab)] == []
        assert pane._tools["c1"].expanded   # plain click still expands args
