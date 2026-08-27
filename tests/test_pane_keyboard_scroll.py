"""Keyboard navigation of the transcript: line scroll + message hopping.

The transcript has no keyboard route at all — `Up`/`Down` belong to the
input's sent-message recall, so reading back over a long turn was a
mouse-only gesture. These bindings are app-level and `priority=True`, which
is what keeps them out of the input's way.
"""
import pytest
from textual.containers import VerticalScroll

from aegis.config import Agent
from aegis.events import AssistantText
from aegis.tui.app import AegisApp
from aegis.tui.widgets import GrowingInput


def _agent():
    return Agent(harness="claude-code", model="opus",
                 effort="high", permission="auto")


class FakeSession:
    def __init__(self):
        self.sent = []
        self.started = self.closed = False
    async def start(self): self.started = True
    async def send(self, text): self.sent.append(text)
    async def events(self):
        if False:
            yield  # pragma: no cover
    async def close(self): self.closed = True


class FakeMCP:
    url = "http://127.0.0.1:0/mcp/"
    def __init__(self):
        self.bound = None
    def bind(self, bridge): self.bound = bridge
    async def start(self): pass
    async def stop(self): pass


def _app():
    return AegisApp({"default": _agent()}, "default",
                    lambda agent, mcp_url, handle: FakeSession(), FakeMCP())


async def _tall_transcript(pilot, pane, n_messages=6, lines=20):
    """Mount `n_messages` distinct blocks, each TALLER than the viewport.

    Deliberate: a block shorter than the viewport cannot be parked on the
    first row, because the container clamps at ``max_scroll_y``. That case
    has its own test; these exercise the ordinary walk.
    """
    for i in range(n_messages):
        # Paragraphs, not lines: these render through rich Markdown, which
        # folds single newlines into one wrapped paragraph.
        body = "\n\n".join(f"msg {i} line {j}" for j in range(lines))
        pane._on_core_event(None, AssistantText(text=body, usage=None))
        pane._flush_streaming()
    await pilot.pause()
    await pilot.pause()


@pytest.mark.asyncio
async def test_alt_arrows_scroll_the_transcript_one_line_at_a_time():
    app = _app()
    async with app.run_test(size=(80, 24)) as pilot:
        pane = app._panes[0]
        await _tall_transcript(pilot, pane)
        t = pane.query_one("#transcript", VerticalScroll)
        assert t.max_scroll_y > 2       # there is somewhere to go

        before = t.scroll_offset.y
        await pilot.press("alt+up")
        await pilot.pause()
        assert t.scroll_offset.y == before - 1

        await pilot.press("alt+down")
        await pilot.pause()
        assert t.scroll_offset.y == before


@pytest.mark.asyncio
async def test_alt_up_does_not_touch_the_input_or_its_history():
    """`Up` recalls a sent message; `alt+up` must not. A scroll gesture that
    also rewrote the draft would be worse than no gesture."""
    app = _app()
    async with app.run_test(size=(80, 24)) as pilot:
        pane = app._panes[0]
        await _tall_transcript(pilot, pane)
        inp = pane.query_one(GrowingInput)
        inp._history.append("a message I sent earlier")
        inp.value = ""
        await pilot.press("alt+up")
        await pilot.pause()
        assert inp.text == ""


@pytest.mark.asyncio
async def test_ctrl_up_parks_the_previous_message_at_the_top_of_the_view():
    """One press from the tail puts the *start* of the last block on the
    first visible row — the whole point of the gesture."""
    app = _app()
    async with app.run_test(size=(80, 24)) as pilot:
        pane = app._panes[0]
        await _tall_transcript(pilot, pane)
        t = pane.query_one("#transcript", VerticalScroll)

        await pilot.press("ctrl+up")
        await pilot.pause()
        assert pane._mounted_blocks[-1].region.y == t.content_region.y

        await pilot.press("ctrl+up")
        await pilot.pause()
        assert pane._mounted_blocks[-2].region.y == t.content_region.y


@pytest.mark.asyncio
async def test_ctrl_down_walks_back_toward_the_tail():
    app = _app()
    async with app.run_test(size=(80, 24)) as pilot:
        pane = app._panes[0]
        await _tall_transcript(pilot, pane)
        t = pane.query_one("#transcript", VerticalScroll)

        for _ in range(3):
            await pilot.press("ctrl+up")
            await pilot.pause()
        assert pane._mounted_blocks[-3].region.y == t.content_region.y

        await pilot.press("ctrl+down")
        await pilot.pause()
        assert pane._mounted_blocks[-2].region.y == t.content_region.y


@pytest.mark.asyncio
async def test_ctrl_down_past_the_last_message_restores_the_live_follow():
    """Scrolling up unsticks the pane; walking back down to the end has to
    re-stick it, or streaming silently stops following."""
    app = _app()
    async with app.run_test(size=(80, 24)) as pilot:
        pane = app._panes[0]
        await _tall_transcript(pilot, pane)
        t = pane.query_one("#transcript", VerticalScroll)

        await pilot.press("ctrl+up")
        await pilot.pause()
        assert pane._stick_to_bottom is False

        for _ in range(4):
            await pilot.press("ctrl+down")
            await pilot.pause()
        assert t.scroll_offset.y == int(t.max_scroll_y)
        assert pane._stick_to_bottom is True


@pytest.mark.asyncio
async def test_short_trailing_messages_still_walk_instead_of_hopping_in_place():
    """The last blocks of a transcript are usually shorter than the viewport,
    so their tops sit past ``max_scroll_y`` and cannot reach the first row.
    Each press must still make progress: pick the nearest top at or above the
    current row, and the scroll position strictly decreases."""
    app = _app()
    async with app.run_test(size=(80, 24)) as pilot:
        pane = app._panes[0]
        for i in range(30):
            pane._on_core_event(None, AssistantText(text=f"m{i}", usage=None))
            pane._flush_streaming()
        await pilot.pause()
        await pilot.pause()
        t = pane.query_one("#transcript", VerticalScroll)
        assert t.scroll_offset.y == int(t.max_scroll_y)

        for _ in range(8):
            await pilot.press("ctrl+up")
            await pilot.pause()
        moved = t.scroll_offset.y
        assert moved < int(t.max_scroll_y)
        tops = {b.region.y for b in pane._mounted_blocks}
        assert t.content_region.y in tops       # parked on a message start

        await pilot.press("ctrl+up")
        await pilot.pause()
        assert t.scroll_offset.y < moved         # and it keeps going


@pytest.mark.asyncio
async def test_alt_end_jumps_straight_back_to_the_live_tail():
    app = _app()
    async with app.run_test(size=(80, 24)) as pilot:
        pane = app._panes[0]
        await _tall_transcript(pilot, pane)
        t = pane.query_one("#transcript", VerticalScroll)

        t.scroll_y = 0
        await pilot.pause()
        assert pane._stick_to_bottom is False

        await pilot.press("alt+end")
        await pilot.pause()
        assert t.scroll_offset.y == int(t.max_scroll_y)
        assert pane._stick_to_bottom is True


@pytest.mark.asyncio
async def test_scroll_keys_are_inert_on_a_tab_with_no_transcript(monkeypatch):
    """A terminal / file tab has no ConversationPane API; the binding must
    no-op there rather than raising into the key handler."""
    app = _app()
    async with app.run_test(size=(80, 24)) as pilot:
        class Dummy:
            pass
        monkeypatch.setattr(AegisApp, "_active",
                            property(lambda self: Dummy()))
        app.action_scroll_transcript(-1)
        app.action_scroll_message(-1)
        app.action_jump_to_tail()
        await pilot.pause()
