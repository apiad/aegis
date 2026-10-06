"""While the mic is open the input is not editable and cannot be sent."""
from __future__ import annotations

import pytest
from textual.app import App, ComposeResult

from aegis.tui.widgets import GrowingInput


class _Host(App):
    """Records submissions through Textual's own message handler.

    Do NOT spy by reassigning ``inp.post_message``: that is the widget's
    message pump, so replacing it stops the widget processing its internal
    messages and ``run_test()`` hangs on teardown rather than failing.
    """

    def __init__(self) -> None:
        super().__init__()
        self.submitted: list[str] = []

    def compose(self) -> ComposeResult:
        yield GrowingInput(placeholder="type…")

    def on_growing_input_submitted(
            self, event: GrowingInput.Submitted) -> None:
        self.submitted.append(event.value)


@pytest.mark.asyncio
async def test_locked_sets_read_only():
    app = _Host()
    async with app.run_test():
        inp = app.query_one(GrowingInput)
        assert inp.locked is False
        assert inp.read_only is False
        inp.locked = True
        assert inp.read_only is True, "the lock rides Textual's read_only"


@pytest.mark.asyncio
async def test_unlocking_restores_editing():
    app = _Host()
    async with app.run_test():
        inp = app.query_one(GrowingInput)
        inp.locked = True
        inp.locked = False
        assert inp.read_only is False


@pytest.mark.asyncio
async def test_typing_is_ignored_while_locked():
    app = _Host()
    async with app.run_test() as pilot:
        inp = app.query_one(GrowingInput)
        inp.value = "kept"
        inp.focus()
        inp.locked = True
        await pilot.press("x", "y", "z")
        await pilot.pause()
        assert inp.value == "kept", "keystrokes must not reach a locked input"


@pytest.mark.asyncio
async def test_submit_is_refused_while_locked():
    """Submit clears the input, and the pending transcript assignment would
    then resurrect the text just sent."""
    app = _Host()
    async with app.run_test() as pilot:
        inp = app.query_one(GrowingInput)
        inp.value = "half a message"
        inp.locked = True
        await inp.action_submit()
        await pilot.pause()
        assert app.submitted == [], "a locked input must not submit"


@pytest.mark.asyncio
async def test_submit_works_again_after_unlock():
    app = _Host()
    async with app.run_test() as pilot:
        inp = app.query_one(GrowingInput)
        inp.value = "a message"
        inp.locked = True
        await inp.action_submit()
        inp.locked = False
        await inp.action_submit()
        await pilot.pause()
        assert app.submitted == ["a message"]
