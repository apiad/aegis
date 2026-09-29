"""A live pane shows every turn's text, including turns it did not send.

The pane mounts a user line when it sends one itself (``_submit``) or when a
text-box message dispatches, and drops claude's ``UserMessage`` echo so that
line is not drawn twice. A turn started anywhere else, such as the opening
prompt the brain sends for ``/spawn``, ``aegis_spawn`` or a queue worker, had
no line mounted, so dropping its echo left the operator looking at an agent
answering a prompt they could not see. Issue #24.
"""

import pytest

from aegis.events import Result, UserMessage
from aegis.queue.schema import InboxMessage
from tests.test_pane_windowing import _app


async def _fresh_pane(app, pilot):
    from aegis.tui.pane import CopyableBlock

    pane = app._panes[0]
    for b in list(pane.query(CopyableBlock)):
        b.remove()
    pane._history.clear()
    pane._mounted_blocks.clear()
    await pilot.pause()
    return pane


def _count(pane, text: str) -> int:
    return sum(text in r.payload for r in pane._history)


@pytest.mark.asyncio
async def test_a_turn_the_pane_did_not_send_shows_its_text():
    """The /spawn case: the brain sent the opening prompt, the pane mounted
    nothing, and the echo is the only record of it reaching the screen."""
    app = _app()
    async with app.run_test() as pilot:
        pane = await _fresh_pane(app, pilot)

        pane._on_core_event(None, UserMessage(text="verify this test"))
        await pilot.pause()

        assert _count(pane, "verify this test") == 1


@pytest.mark.asyncio
async def test_a_dispatched_text_box_line_is_not_drawn_twice():
    app = _app()
    async with app.run_test() as pilot:
        pane = await _fresh_pane(app, pilot)

        msg = InboxMessage(sender="user", timestamp="t", body="work on aegis")
        pane._on_core_dispatch(None, [msg])
        pane._on_core_event(None, UserMessage(text="work on aegis"))
        await pilot.pause()

        assert _count(pane, "work on aegis") == 1


@pytest.mark.asyncio
async def test_an_inbox_turn_is_not_redrawn_from_its_echo():
    """A handoff renders as an inbox block on arrival; the turn it starts
    echoes the rendered batch, which must not appear a second time."""
    app = _app()
    async with app.run_test() as pilot:
        pane = await _fresh_pane(app, pilot)

        msg = InboxMessage(sender="agent:peer", timestamp="t", body="take this over")
        pane._on_core_inbox(None, msg)
        pane._on_core_dispatch(None, [msg])
        pane._on_core_event(None, UserMessage(text="> from agent:peer\ntake this over"))
        await pilot.pause()

        assert _count(pane, "take this over") == 1


@pytest.mark.asyncio
async def test_the_expected_echo_does_not_outlive_its_turn():
    """A turn the pane sent, then a turn it did not: the second one's text
    must still show. An expectation left over from the first turn would eat
    it."""
    app = _app()
    async with app.run_test() as pilot:
        pane = await _fresh_pane(app, pilot)

        msg = InboxMessage(sender="user", timestamp="t", body="first")
        pane._on_core_dispatch(None, [msg])
        pane._on_core_event(None, Result(duration_ms=1, is_error=False))
        pane._on_core_event(None, UserMessage(text="second"))
        await pilot.pause()

        assert _count(pane, "second") == 1
