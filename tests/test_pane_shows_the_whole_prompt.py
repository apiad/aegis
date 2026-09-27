"""What the operator was sent is what the operator can read back.

A Rich-console assertion on ``render_user_block`` is one layer short of the
claim: the pane mounts that renderable into a Textual screen, and the bug this
guards was only ever visible there — three blank rows where two instruction
blocks should have been. So these drive a real pane and read the screen.

The bug: Markdown opens an HTML block on a line-leading tag, Rich's Markdown
renderer has no handler for one, and the whole block rendered as nothing while
the agent received every word. `/spawn`'s composed opening pushes a transcript
tail through this path, and transcript tails are full of such tags.
"""

from __future__ import annotations

import pytest

from tests.test_pane_windowing import _app


def _screen_text(app) -> str:
    """Everything the terminal is showing, as plain text."""
    return "\n".join(
        "".join(seg.text for seg in line)
        for line in app.screen._compositor.render_strips()
    )


@pytest.mark.asyncio
async def test_the_pane_shows_a_tag_delimited_block_it_was_sent():
    app = _app()
    async with app.run_test(size=(120, 50)) as pilot:
        pane = app._panes[0]
        pane._submit(
            "The operator's task: ship the fix\n\n"
            "<system-reminder>\n"
            "RULE ONE: never stage broadly, always name paths\n"
            "</system-reminder>\n\n"
            "Final line the operator typed."
        )
        await pilot.pause()

        shown = _screen_text(app)
        assert "RULE ONE" in shown, "the rule block never reached the screen"
        assert "Final line" in shown


@pytest.mark.asyncio
async def test_the_pane_shows_a_spawn_tail_one_item_per_line():
    """The composed opening a `/spawn` hands its new agent, on screen."""
    from aegis.peer import compose_spawn

    tail = (
        "user: the round-trip test keeps failing on the second assert\n"
        "<system-reminder>never stage broadly</system-reminder>\n"
        "assistant: because assemble() fills backwards from the newest event"
    )
    body = compose_spawn(
        source="alpha",
        slug="opus",
        prompt="verify this test",
        tail=tail,
        header="last 3 of 143 turns",
    )

    app = _app()
    async with app.run_test(size=(120, 50)) as pilot:
        pane = app._panes[0]
        pane._submit(body)
        await pilot.pause()

        shown = _screen_text(app)
        assert "the round-trip test keeps failing" in shown
        assert "never stage broadly" in shown
        assert "verify this test" in shown
        glued = [
            ln
            for ln in shown.splitlines()
            if "keeps failing" in ln and "fills backwards" in ln
        ]
        assert not glued, "the tail folded into one line on screen"
