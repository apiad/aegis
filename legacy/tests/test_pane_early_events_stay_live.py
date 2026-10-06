"""A pane that hears its session before it is mounted keeps drawing after.

Issue #16.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from aegis.events import AssistantText, ToolUse
from aegis.state.session_log import EventReplay
from aegis.tui.pane import ConversationPane, CopyableBlock
from tests.test_pane_windowing import _agent, _app


def _ev(n):
    return ToolUse(name="Bash", summary=f"step {n}", tool_call_id=f"t{n}")


def _drawn(pane):
    return [b._text_payload for b in pane._mounted_blocks]


def _pane(app):
    return ConversationPane(None, _agent(), "default", "eval-une-pr109",
                            app._palette, core=app._active._core,
                            replay=EventReplay(events=[], interrupted=False),
                            project_root=Path.cwd())


@pytest.mark.asyncio
async def test_a_tab_opened_in_the_background_keeps_drawing_after_first_look():
    """The /spawn shape, and the one in the issue. The brain's observer mounts
    the new tab hidden, the agent's first events draw into it, and the
    operator opens it later. That first look ran `_mount_replay`, which reset
    `_window_end` to the length of the replay (zero for a fresh session), and
    every block after it was recorded and never drawn. The transcript stopped
    where the first look found it."""
    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        pane = _pane(app)
        cs = app.query_one("ContentSwitcher")
        pane.display = False
        await cs.mount(pane)
        await pilot.pause()

        pane._on_core_event(None, AssistantText(text="Reading the PR"))
        pane._on_core_event(None, _ev(35))
        await pilot.pause()

        cs.current = pane.id
        await pilot.pause()

        pane._on_core_event(None, _ev(36))
        await pilot.pause()

        drawn = _drawn(pane)
        for n in (35, 36):
            assert any(f"step {n}" in p for p in drawn), (
                f"step {n} is in history but not drawn: {drawn}")
        on_screen = [b._text_payload for b in pane.query(CopyableBlock)]
        assert sum("step 35" in p for p in on_screen) == 1, (
            f"the first look drew step 35 a second time: {on_screen}")


@pytest.mark.asyncio
async def test_events_that_arrive_before_mount_are_drawn_on_first_look():
    """The same reset from the other side: events recorded before the pane
    was in the tree were prepended behind the replay and never mounted."""
    app = _app()
    async with app.run_test() as pilot:
        await pilot.pause()
        pane = _pane(app)
        pane._on_core_event(None, AssistantText(text="Reading the PR"))
        pane._on_core_event(None, _ev(1))

        cs = app.query_one("ContentSwitcher")
        await cs.mount(pane)
        cs.current = pane.id
        await pilot.pause()

        pane._on_core_event(None, _ev(2))
        await pilot.pause()

        drawn = _drawn(pane)
        for n in (1, 2):
            assert any(f"step {n}" in p for p in drawn), (
                f"step {n} is in history but not drawn: {drawn}")
