"""The pane's replay logic mounts one renderable per replay event,
plus an interrupted marker iff replay.interrupted.

Tests use the pure ``replay_blocks`` helper — no Textual app required.
"""
from rich.markdown import Markdown
from rich.text import Text

from aegis.events import AssistantText, Result, SystemInit
from aegis.state.session_log import EventReplay
from aegis.tui.pane import replay_blocks


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
