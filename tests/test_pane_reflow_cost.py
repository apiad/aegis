"""Reflow cost is linear in the number of MOUNTED WIDGETS.

Textual rebuilds the whole compositor map on any layout change — every
keystroke in the input box, every scroll line, every streamed delta.
Measured on zion at a full transcript window (N_MAX = 300 blocks), one
reflow costs ~300 ms with two widgets per block and ~120 ms with one,
so the wrapper widget inside each transcript cell was doubling the cost
of every interaction in a long thread.

These assertions are structural on purpose: a wall-clock assertion would
flake on a loaded box (see docs/superpowers/specs/2026-07-29-tui-
performance-audit.md, "A note for whoever implements this").
"""
import pytest
from rich.text import Text

from aegis.config import Agent
from aegis.tui.app import AegisApp
from aegis.tui.pane import CopyableBlock


def _agent():
    return Agent(harness="claude-code", model="opus",
                 effort="high", permission="auto")


class FakeSession:
    def __init__(self):
        self.sent = []
    async def start(self): pass
    async def send(self, text): self.sent.append(text)
    async def events(self):
        if False:
            yield  # pragma: no cover
    async def close(self): pass


class FakeMCP:
    url = "http://127.0.0.1:0/mcp/"
    def bind(self, bridge): pass
    async def start(self): pass
    async def stop(self): pass


def _app():
    return AegisApp({"default": _agent()}, "default",
                    lambda *a, **kw: FakeSession(), FakeMCP())


@pytest.mark.asyncio
async def test_a_transcript_block_is_a_single_widget():
    """One block, one entry in the compositor map — no wrapper child."""
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        block = pane._mount_block(Text("hello"), "hello")
        await pilot.pause()

        assert isinstance(block, CopyableBlock)
        assert list(block.children) == [], (
            "a transcript block must render its own content; a child widget "
            "doubles the per-block cost of every screen reflow")


@pytest.mark.asyncio
async def test_status_bar_updates_never_ask_for_layout():
    """The bar is `height: 1` and `fit()` trims to the available width, so
    its size cannot change — and a layout refresh from it would rebuild the
    whole compositor map once per streamed delta."""
    from aegis.tui.widgets import StatusBar

    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        bar = pane.query_one(StatusBar)
        await pilot.pause()

        asked_for_layout = []
        orig = type(bar).refresh

        def spy(self, *regions, repaint=True, layout=False, recompose=False,
                **kw):
            if layout or recompose:
                asked_for_layout.append(True)
            return orig(self, *regions, repaint=repaint, layout=layout,
                        recompose=recompose, **kw)

        bar.refresh = spy.__get__(bar)
        for i in range(5):
            bar.set_metrics(f"{i} tokens")
        await pilot.pause()

        assert asked_for_layout == []


@pytest.mark.asyncio
async def test_eviction_prunes_in_one_batch():
    """Textual fires `parent.refresh(layout=True)` once per prune, so
    evicting EVICT_BATCH blocks one at a time was a burst of EVICT_BATCH
    full-screen reflows every time the window filled."""
    from aegis.tui.pane import EVICT_BATCH

    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        for i in range(EVICT_BATCH + 5):
            pane._mount_block(Text(f"line {i}"), f"line {i}")
        await pilot.pause()

        prunes = []
        orig = app._prune
        app._prune = lambda *nodes, **kw: (prunes.append(len(nodes)),
                                           orig(*nodes, **kw))[1]

        pane._evict_top(EVICT_BATCH)
        await pilot.pause()
        await pilot.pause()

        assert prunes == [EVICT_BATCH], (
            f"expected one prune of {EVICT_BATCH} blocks, got {prunes}")
        assert len(pane._mounted_blocks) == 5


@pytest.mark.asyncio
async def test_scroll_up_load_mounts_in_one_batch():
    """Same shape on the way back in: one mount call, not LOAD_BATCH."""
    from aegis.state.session_log import EventReplay
    from aegis.events import ToolUse
    from aegis.tui.pane import LOAD_BATCH, REPLAY_TAIL

    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        for b in list(pane.query(CopyableBlock)):
            b.remove()
        pane._history.clear()
        pane._mounted_blocks.clear()
        pane._window_start = 0
        pane._replay = EventReplay(
            events=[ToolUse(name="Read", summary=f"f{i}.py", kind="read")
                    for i in range(LOAD_BATCH * 3)],
            interrupted=False)
        await pilot.pause()
        pane._mount_replay()
        await pilot.pause()
        assert pane._window_start == len(pane._history) - REPLAY_TAIL

        transcript = pane._transcript()
        mounts = []
        orig = transcript.mount
        transcript.mount = lambda *w, **kw: (mounts.append(len(w)),
                                             orig(*w, **kw))[1]

        pane._load_older()
        await pilot.pause()
        await pilot.pause()

        assert mounts == [LOAD_BATCH], (
            f"expected one mount of {LOAD_BATCH} blocks, got {mounts}")
        assert len(pane._mounted_blocks) == REPLAY_TAIL + LOAD_BATCH


@pytest.mark.asyncio
async def test_updating_a_block_keeps_it_a_single_widget():
    """Streaming updates must not mount anything either."""
    app = _app()
    async with app.run_test() as pilot:
        pane = app._panes[0]
        block = pane._mount_block(Text("tok"), "tok")
        await pilot.pause()
        for i in range(5):
            block.update_content(Text(f"tok{i}"), f"tok{i}")
            await pilot.pause()

        assert list(block.children) == []
        assert block.text_payload() == "tok4"
