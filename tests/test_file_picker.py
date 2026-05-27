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


class _Host(App):
    def __init__(self, prefill: str = "") -> None:
        super().__init__()
        self._prefill = prefill

    def compose(self) -> ComposeResult:
        yield FilePickerModal(prefill=self._prefill)
