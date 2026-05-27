from __future__ import annotations

import os
from pathlib import Path

import pytest
from textual.app import App, ComposeResult

from aegis.tui.picker import FilePickerModal


@pytest.mark.asyncio
async def test_file_picker_mounts(tmp_path: Path):
    (tmp_path / "hello.py").write_text("print('hi')")
    os.chdir(tmp_path)
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import Input
        inp = app.query_one(Input)
        assert inp.value == ""


@pytest.mark.asyncio
async def test_file_picker_prefill(tmp_path: Path):
    (tmp_path / "myfile.py").write_text("x = 1")
    os.chdir(tmp_path)
    app = _Host(prefill="myfile")
    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import Input
        inp = app.query_one(Input)
        assert inp.value == "myfile"


@pytest.mark.asyncio
async def test_file_picker_escape_dismisses(tmp_path: Path):
    os.chdir(tmp_path)

    class _Wrapper(App):
        dismissed: bool = False

        async def on_mount(self) -> None:
            self.push_screen(FilePickerModal(),
                             callback=self._on_dismiss)

        def _on_dismiss(self, result) -> None:
            self.dismissed = True
            self.exit()

    app = _Wrapper()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        for _ in range(5):
            await pilot.pause()

    assert app.dismissed


def test_extract_backtick_tokens():
    from aegis.tui.pane import _extract_backtick_tokens
    assert _extract_backtick_tokens("see `foo.py` for details") == ["foo.py"]
    assert _extract_backtick_tokens("no backticks here") == []
    assert _extract_backtick_tokens("`a.py` and `b.py`") == ["a.py", "b.py"]
    assert _extract_backtick_tokens("") == []


@pytest.mark.asyncio
async def test_file_picker_uses_indexer(tmp_path: Path):
    """Picker reads from app._file_indexer when available."""
    from aegis.tui.file_index import FileIndexer

    (tmp_path / "indexed.py").write_text("x")

    class _AppWithIndexer(App):
        def __init__(self) -> None:
            super().__init__()
            self._file_indexer = FileIndexer()
            self._file_indexer.start(tmp_path)
            self._file_indexer._ready.wait(timeout=3)

        def compose(self) -> ComposeResult:
            yield FilePickerModal()

    app = _AppWithIndexer()
    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import OptionList
        ol = app.query_one("#fp-list", OptionList)
        await pilot.pause()
        option_ids = [ol.get_option_at_index(i).id
                      for i in range(ol.option_count)]
        assert any("indexed.py" in (oid or "") for oid in option_ids)


@pytest.mark.asyncio
async def test_token_chooser_returns_selected():
    result_holder: list = []

    class _Wrapper(App):
        async def on_mount(self) -> None:
            self.push_screen(
                _TokenChooser(["src/foo.py", "tests/bar.py"]),
                callback=lambda r: result_holder.append(r) or self.exit())

    from aegis.tui.picker import _TokenChooser
    app = _Wrapper()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        for _ in range(5):
            await pilot.pause()

    assert result_holder and result_holder[0] == "src/foo.py"


@pytest.mark.asyncio
async def test_token_chooser_escape_returns_none():
    result_holder: list = []

    class _Wrapper(App):
        async def on_mount(self) -> None:
            self.push_screen(
                _TokenChooser(["a.py", "b.py"]),
                callback=lambda r: result_holder.append(r) or self.exit())

    from aegis.tui.picker import _TokenChooser
    app = _Wrapper()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        for _ in range(5):
            await pilot.pause()

    assert result_holder == [None]


class _Host(App):
    def __init__(self, prefill: str = "") -> None:
        super().__init__()
        self._prefill = prefill

    def compose(self) -> ComposeResult:
        yield FilePickerModal(prefill=self._prefill)
