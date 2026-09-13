"""A pane subscribes before it is mounted, and the session does not wait.

`ConversationPane.__init__` wires its six observers to the core, and
`_mount_brain_pane` builds panes over sessions the brain is already
running. So a view attaching to a busy daemon receives events in the
window between the pane being constructed and its transcript being
mounted.

Two things went wrong in that window, and both were invisible until an
adopted pane started carrying a replay.

`_mount_block` mounted unconditionally, so it raised MountError against a
`#transcript` that exists but is not mounted yet. The emitter guard caught
it and logged a traceback per event, which is how it was found: an
`aegis.log` full of them after a normal attach.

Then `_mount_replay` assigned `self._history = records`, dropping whatever
had arrived while it was not looking. That is worse than the exception,
because nothing reports it: the turn the agent produced during the attach
is simply missing from the transcript.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from rich.text import Text

from aegis.events import AssistantText, Result, SystemInit
from aegis.state.session_log import EventReplay
from aegis.tui.pane import ConversationPane
from tests.test_pane_windowing import _agent, _app


def _replay() -> EventReplay:
    return EventReplay(
        events=[SystemInit(session_id="s"),
                AssistantText(text="from before the attach", usage=None),
                Result(duration_ms=1, is_error=False)],
        interrupted=False)


@pytest.mark.asyncio
async def test_an_event_arriving_before_mount_does_not_raise():
    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        pane = ConversationPane(None, _agent(), "default", "busy-brain",
                                app._palette, core=app._active._core,
                                replay=_replay(), project_root=Path.cwd())
        # Not mounted yet, which is exactly the window `_mount_brain_pane`
        # opens over a session that is already running.
        pane._mount_block(Text("live"), "live")


@pytest.mark.asyncio
async def test_the_replay_does_not_discard_what_arrived_while_it_waited():
    """The silent half. Both must survive: the conversation from disk and
    the turn that landed during the attach."""
    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        pane = ConversationPane(None, _agent(), "default", "busy-brain2",
                                app._palette, core=app._active._core,
                                replay=_replay(), project_root=Path.cwd())
        pane._mount_block(Text("live block"), "live-payload")
        assert len(pane._history) == 1

        cs = app.query_one("ContentSwitcher")
        await cs.mount(pane)
        cs.current = pane.id
        await pilot.pause()
        await pilot.pause()

        payloads = [r.payload for r in pane._history]
        assert any("live-payload" in p for p in payloads), (
            "the replay overwrote the event that arrived during the attach; "
            f"history is {payloads}")
        assert len(pane._history) > 1, \
            "the prior conversation was not replayed at all"
