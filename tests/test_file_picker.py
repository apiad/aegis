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
    # Repeated tokens collapse to first occurrence — they would otherwise
    # collide on OptionList id and crash the chooser.
    assert _extract_backtick_tokens(
        "`a.py` then `b.py` then `a.py` again") == ["a.py", "b.py"]


@pytest.mark.asyncio
async def test_token_chooser_survives_duplicate_tokens():
    """Repeated tokens used to raise DuplicateID and crash the app."""
    from aegis.tui.picker import _TokenChooser
    result_holder: list = []

    class _Wrapper(App):
        async def on_mount(self) -> None:
            self.push_screen(
                _TokenChooser(["foo.py", "bar.py", "foo.py"]),
                callback=lambda r: result_holder.append(r) or self.exit())

    app = _Wrapper()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        for _ in range(5):
            await pilot.pause()

    assert result_holder and result_holder[0] == "foo.py"


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
async def test_file_picker_top_match_highlighted(tmp_path: Path):
    """After filtering, the first match is always highlighted so Enter
    opens it without arrow-key navigation."""
    (tmp_path / "alpha.py").write_text("a")
    (tmp_path / "beta.py").write_text("b")
    os.chdir(tmp_path)
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import Input, OptionList
        inp = app.query_one(Input)
        inp.value = "alpha"
        await pilot.pause()
        ol = app.query_one("#fp-list", OptionList)
        assert ol.option_count >= 1
        assert ol.highlighted == 0


@pytest.mark.asyncio
async def test_file_picker_arrow_keys_navigate(tmp_path: Path):
    """Down/up arrows move highlight while Input keeps focus."""
    for name in ("a.py", "b.py", "c.py"):
        (tmp_path / name).write_text("x")
    os.chdir(tmp_path)
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import Input, OptionList
        inp = app.query_one(Input)
        ol = app.query_one("#fp-list", OptionList)
        assert ol.highlighted == 0
        assert inp.has_focus
        await pilot.press("down")
        await pilot.pause()
        assert ol.highlighted == 1
        await pilot.press("down")
        await pilot.pause()
        assert ol.highlighted == 2
        await pilot.press("up")
        await pilot.pause()
        assert ol.highlighted == 1
        assert inp.has_focus


@pytest.mark.asyncio
async def test_file_picker_enter_opens_top_match(tmp_path: Path):
    """Enter on a prefilled query dismisses with the top-match path."""
    (tmp_path / "target.py").write_text("x")
    (tmp_path / "other.py").write_text("y")
    os.chdir(tmp_path)
    result: list = []

    class _Wrapper(App):
        async def on_mount(self) -> None:
            self.push_screen(FilePickerModal(prefill="target"),
                             callback=lambda r: (result.append(r),
                                                 self.exit()))

    app = _Wrapper()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        for _ in range(5):
            await pilot.pause()

    assert result and result[0] is not None
    assert result[0].name == "target.py"


@pytest.mark.asyncio
async def test_file_picker_indexer_poll_does_not_clobber_input(tmp_path: Path):
    """After the indexer is ready and boot has run, the poll path must
    not refire and reset the Input value / option list."""
    from aegis.tui.file_index import FileIndexer

    (tmp_path / "alpha.py").write_text("a")
    (tmp_path / "beta.py").write_text("b")

    class _AppWithIndexer(App):
        def __init__(self) -> None:
            super().__init__()
            self._file_indexer = FileIndexer()
            self._file_indexer.start(tmp_path)
            self._file_indexer._ready.wait(timeout=3)

        def compose(self) -> ComposeResult:
            yield FilePickerModal()

    os.chdir(tmp_path)
    app = _AppWithIndexer()
    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import Input, OptionList
        inp = app.query_one(Input)
        ol = app.query_one("#fp-list", OptionList)
        inp.value = "alpha"
        await pilot.pause()
        # Give any stray timers a chance to fire.
        for _ in range(10):
            await pilot.pause(0.05)
        assert inp.value == "alpha"
        option_ids = [ol.get_option_at_index(i).id
                      for i in range(ol.option_count)]
        assert all("alpha" in (oid or "") for oid in option_ids)


def test_resolve_unique_indexed_match_exact_path():
    from aegis.tui.picker import resolve_unique_match
    paths = ["src/foo.py", "tests/foo.py", "src/bar.py"]
    assert resolve_unique_match("src/foo.py", paths) == "src/foo.py"


def test_resolve_unique_indexed_match_basename_unique():
    from aegis.tui.picker import resolve_unique_match
    paths = ["src/foo.py", "tests/bar.py"]
    assert resolve_unique_match("bar.py", paths) == "tests/bar.py"


def test_resolve_unique_indexed_match_basename_ambiguous():
    from aegis.tui.picker import resolve_unique_match
    paths = ["src/foo.py", "tests/foo.py"]
    assert resolve_unique_match("foo.py", paths) is None


def test_resolve_unique_indexed_match_missing():
    from aegis.tui.picker import resolve_unique_match
    paths = ["src/foo.py"]
    assert resolve_unique_match("nope.py", paths) is None


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
