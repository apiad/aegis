"""The pane's replay logic mounts one renderable per replay event,
plus an interrupted marker iff replay.interrupted.

Tests use the pure ``replay_blocks`` helper — no Textual app required.
"""
import pytest
from rich.markdown import Markdown
from rich.text import Text

from aegis.events import (
    AssistantText, AssistantThinking, Result, SystemInit, ToolUse,
)
from aegis.state.session_log import EventReplay
from aegis.tui.pane import replay_blocks
from tests.test_pane_windowing import _app


def _block_text(block) -> str:
    """Extract a string representation suitable for content assertions.

    ``Markdown`` objects don't expose their source text via str(); we read
    the private ``markup`` attribute instead.  ``Text`` objects render via
    ``plain``.  Fall back to repr() for anything else.
    """
    if isinstance(block, Markdown):
        return block.markup
    if isinstance(block, Text):
        return block.plain
    return repr(block)


def test_replay_blocks_for_completed_turn():
    rep = EventReplay(
        events=[
            SystemInit(session_id="s"),
            AssistantText(text="hi", usage=None),
            Result(duration_ms=1, is_error=False),
        ],
        interrupted=False,
    )
    blocks = replay_blocks(rep)
    rendered = "\n".join(_block_text(b) for b in blocks)
    # AssistantText content is present.
    assert "hi" in rendered
    # No interrupted marker for a completed turn.
    assert "interrupted" not in rendered.lower()


def test_replay_blocks_appends_interrupted_marker():
    rep = EventReplay(
        events=[
            SystemInit(session_id="s"),
            AssistantText(text="started…", usage=None),
        ],
        interrupted=True,
    )
    blocks = replay_blocks(rep)
    rendered = "\n".join(_block_text(b) for b in blocks)
    assert "started" in rendered
    assert "interrupted" in rendered.lower()


def test_replay_blocks_empty_for_empty_replay():
    assert replay_blocks(EventReplay(events=[], interrupted=False)) == []


def test_replay_blocks_reports_damaged_records():
    """A shortened transcript must say it was shortened — silently dropping
    turns reads as 'the conversation was always this short'."""
    rep = EventReplay(
        events=[AssistantText(text="kept", usage=None)],
        interrupted=False, damaged=3, recovered=1,
    )
    rendered = "\n".join(_block_text(b) for b in replay_blocks(rep))
    assert "kept" in rendered
    assert "3" in rendered and "damaged" in rendered.lower()
    assert "1 recovered" in rendered


def test_replay_blocks_quiet_when_undamaged():
    rep = EventReplay(events=[AssistantText(text="kept", usage=None)],
                      interrupted=False)
    rendered = "\n".join(_block_text(b) for b in replay_blocks(rep))
    assert "damaged" not in rendered.lower()


def test_replay_blocks_skips_none_renderables():
    """SystemInit renders to None — it must not appear as a block."""
    rep = EventReplay(
        events=[SystemInit(session_id="s")],
        interrupted=False,
    )
    blocks = replay_blocks(rep)
    assert blocks == []


def test_replay_blocks_empty_replay_with_interrupted_flag():
    """An empty event list with interrupted=True still emits the marker
    (edge case: session started but no events were written before crash)."""
    rep = EventReplay(events=[], interrupted=True)
    blocks = replay_blocks(rep)
    rendered = "\n".join(str(b) for b in blocks)
    assert "interrupted" in rendered.lower()


def test_replay_blocks_coalesces_chunk_runs():
    """OpenCode persists token-level chunks (~116 thoughts per turn).
    Replay must coalesce same-(kind, message_id) runs into one block,
    not render one line per token."""
    rep = EventReplay(
        events=[
            AssistantThinking(text="Let ", message_id="m_t"),
            AssistantThinking(text="me ", message_id="m_t"),
            AssistantThinking(text="think.", message_id="m_t"),
            AssistantText(text="Done.", message_id="m_r"),
            Result(duration_ms=1, is_error=False),
        ],
        interrupted=False,
    )
    blocks = replay_blocks(rep)
    # 3 thinking chunks coalesce → 1 block. Plus 1 text, plus 1 result.
    assert len(blocks) == 3
    rendered = "\n".join(_block_text(b) for b in blocks)
    # The 3 chunks became ONE compact 'thought' block (not 3 token lines);
    # the merged full text is covered by test_event_coalesce + the copy payload.
    assert rendered.count("thought") == 1
    assert "Done." in rendered


def test_replay_blocks_chunk_run_broken_by_tooluse():
    """A ToolUse between two same-id chunk runs must break the merge —
    you'd otherwise lose the tool call's visual position in the
    transcript."""
    rep = EventReplay(
        events=[
            AssistantText(text="reading ", message_id="m"),
            AssistantText(text="file ", message_id="m"),
            ToolUse(name="Read", summary="x.py", kind="read"),
            AssistantText(text="done", message_id="m"),
            Result(duration_ms=1, is_error=False),
        ],
        interrupted=False,
    )
    blocks = replay_blocks(rep)
    # 2 text chunks merge, ToolUse, 1 text, Result → 4 blocks
    assert len(blocks) == 4


# --- the real replay path -------------------------------------------
# The tests above use `replay_blocks`, which has no production caller. What
# Ctrl+R actually runs is _mount_replay, so the user-turn behaviour is
# asserted against that.

@pytest.mark.asyncio
async def test_mount_replay_shows_the_users_own_turns():
    """Reopening a conversation must read as a dialogue. The user's lines are
    mounted live at send time and those widgets persist, so this only breaks
    when the transcript is rebuilt from the log — which is exactly what
    Ctrl+R does."""
    from aegis.events import UserMessage
    from aegis.tui.pane import CopyableBlock

    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        for b in list(pane.query(CopyableBlock)):
            b.remove()
        pane._history.clear()
        pane._mounted_blocks.clear()
        pane._window_start = 0
        pane._replay = EventReplay(
            events=[UserMessage(text="work on aegis"),
                    AssistantText(text="on it"),
                    Result(duration_ms=1, is_error=False)],
            interrupted=False)
        await pilot.pause()
        pane._mount_replay()
        await pilot.pause()

        payloads = "\n".join(r.payload for r in pane._history)
        assert "work on aegis" in payloads
        assert "on it" in payloads


@pytest.mark.asyncio
async def test_live_pane_does_not_echo_the_user_line_twice():
    """claude replays the user's message back to us, so once UserMessage
    renders, the live pane would mount it a second time on top of the line it
    already mounted at send time. Replay needs the event; the live pane
    already has the line."""
    from aegis.events import UserMessage
    from aegis.tui.pane import CopyableBlock

    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        for b in list(pane.query(CopyableBlock)):
            b.remove()
        pane._history.clear()
        pane._mounted_blocks.clear()
        await pilot.pause()

        pane._submit("work on aegis")          # mounts the user's line
        await pilot.pause()
        after_send = sum("work on aegis" in r.payload for r in pane._history)

        pane._on_core_event(None, UserMessage(text="work on aegis"))  # echo
        await pilot.pause()
        after_echo = sum("work on aegis" in r.payload for r in pane._history)

        assert after_send == 1
        assert after_echo == 1, "harness echo re-mounted the user's line"
