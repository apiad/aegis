"""GrowingInput key routing: enqueue vs interrupt submit, and newline keys."""
from __future__ import annotations

import pytest
from textual.app import App, ComposeResult

from aegis.tui.widgets import GrowingInput


class _Host(App):
    def __init__(self) -> None:
        super().__init__()
        self.kinds: list[str] = []

    def compose(self) -> ComposeResult:
        yield GrowingInput(id="inp")

    def on_growing_input_submitted(self,
                                   event: GrowingInput.Submitted) -> None:
        self.kinds.append(event.kind)


@pytest.mark.asyncio
async def test_enter_submits_enqueue():
    app = _Host()
    async with app.run_test() as pilot:
        inp = app.query_one(GrowingInput)
        inp.focus()
        inp.value = "hello"
        await pilot.press("enter")
        await pilot.pause()
        assert app.kinds == ["enqueue"]


@pytest.mark.asyncio
async def test_alt_enter_submits_interrupt():
    app = _Host()
    async with app.run_test() as pilot:
        inp = app.query_one(GrowingInput)
        inp.focus()
        inp.value = "urgent"
        await pilot.press("alt+enter")
        await pilot.pause()
        assert app.kinds == ["interrupt"]


@pytest.mark.asyncio
async def test_alt_enter_no_longer_inserts_newline():
    app = _Host()
    async with app.run_test() as pilot:
        inp = app.query_one(GrowingInput)
        inp.focus()
        inp.value = "line"
        await pilot.press("alt+enter")
        await pilot.pause()
        assert "\n" not in inp.text


@pytest.mark.asyncio
async def test_ctrl_j_inserts_newline():
    app = _Host()
    async with app.run_test() as pilot:
        inp = app.query_one(GrowingInput)
        inp.focus()
        inp.value = "line"
        inp.move_cursor(inp.document.end)
        await pilot.press("ctrl+j")
        await pilot.pause()
        assert "\n" in inp.text

