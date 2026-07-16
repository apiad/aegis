"""GrowingInput history ring: boundary-aware Up/Down recall, draft stash."""
from __future__ import annotations

import pytest
from textual.app import App, ComposeResult

from aegis.tui.widgets import GrowingInput


class _Host(App):
    def compose(self) -> ComposeResult:
        yield GrowingInput(id="inp")

    def on_growing_input_submitted(self,
                                   event: GrowingInput.Submitted) -> None:
        pass


async def _send(app, pilot, text: str) -> None:
    inp = app.query_one(GrowingInput)
    inp.value = text
    await inp.action_submit("enqueue")
    inp.value = ""            # the pane clears after submit; mimic it here
    await pilot.pause()


@pytest.mark.asyncio
async def test_up_recalls_previous_then_older():
    app = _Host()
    async with app.run_test() as pilot:
        inp = app.query_one(GrowingInput)
        inp.focus()
        await _send(app, pilot, "first")
        await _send(app, pilot, "second")

        await pilot.press("up")
        await pilot.pause()
        assert inp.text == "second"
        await pilot.press("up")
        await pilot.pause()
        assert inp.text == "first"
        # Already oldest: another Up stays put.
        await pilot.press("up")
        await pilot.pause()
        assert inp.text == "first"


@pytest.mark.asyncio
async def test_down_past_newest_restores_draft():
    app = _Host()
    async with app.run_test() as pilot:
        inp = app.query_one(GrowingInput)
        inp.focus()
        await _send(app, pilot, "old")
        inp.value = "half-typed draft"
        inp.move_cursor(inp.document.end)

        await pilot.press("up")       # enter recall — stashes the draft
        await pilot.pause()
        assert inp.text == "old"
        await pilot.press("down")     # past newest — restore draft, exit recall
        await pilot.pause()
        assert inp.text == "half-typed draft"


@pytest.mark.asyncio
async def test_up_mid_buffer_moves_cursor_not_history():
    app = _Host()
    async with app.run_test() as pilot:
        inp = app.query_one(GrowingInput)
        inp.focus()
        await _send(app, pilot, "history entry")
        inp.value = "line one\nline two"
        inp.move_cursor(inp.document.end)   # on the last line, not first

        await pilot.press("up")             # cursor moves up a line, no recall
        await pilot.pause()
        assert inp.text == "line one\nline two"


@pytest.mark.asyncio
async def test_consecutive_duplicate_sends_collapse():
    app = _Host()
    async with app.run_test() as pilot:
        inp = app.query_one(GrowingInput)
        inp.focus()
        await _send(app, pilot, "same")
        await _send(app, pilot, "same")
        assert inp._history == ["same"]
