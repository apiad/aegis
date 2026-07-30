"""The mounted window stays bounded even when you are scrolled up.

Audit finding 5: `_mount_block` nests the N_MAX check inside the
`_stick_to_bottom` branch, so reading back through a thread while an
agent is working mounts every new block and never evicts. Measured at
252 -> 652 blocks and still climbing; on a resumed session with thousands
of records there is no bound at all.

Two prior attempts failed by evicting the wrong end: eviction from the
top fights `_load_older`, which re-mounts exactly what was dropped. The
window therefore needs a second edge, and eviction has to take from
whichever end is furthest from the viewport.
"""
import pytest
from rich.text import Text

from aegis.config import Agent
from aegis.tui.app import AegisApp
from aegis.transcript_constants import EVICT_BATCH, N_MAX


def _agent():
    return Agent(harness="claude-code", model="opus",
                 effort="high", permission="auto")


class FakeSession:
    def __init__(self): self.sent = []
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


CEILING = N_MAX + EVICT_BATCH      # eviction is batched, so allow one batch


@pytest.mark.asyncio
async def test_window_stays_bounded_while_scrolled_up():
    """A turn that streams hundreds of blocks while the user reads back
    must not mount all of them."""
    app = _app()
    async with app.run_test(size=(120, 40)) as pilot:
        pane = app._panes[0]
        for i in range(N_MAX):
            pane._mount_block(Text(f"history {i}"), f"history {i}")
        await pilot.pause()

        # Scroll up to read: stickiness drops.
        pane._stick_to_bottom = False
        pane._transcript().scroll_to(y=10, animate=False)
        await pilot.pause()

        for i in range(400):
            pane._mount_block(Text(f"live {i}"), f"live {i}")
        await pilot.pause()

        assert len(pane._mounted_blocks) <= CEILING, (
            f"window grew to {len(pane._mounted_blocks)} while scrolled up")
        # Nothing is lost — history keeps every record.
        assert len(pane._history) == N_MAX + 400


@pytest.mark.asyncio
async def test_mounted_blocks_always_match_the_window_slice():
    """_mounted_blocks must stay in lockstep with
    _history[_window_start:_window_end], or hit-testing and _load_older
    index the wrong records."""
    app = _app()
    async with app.run_test(size=(120, 40)) as pilot:
        pane = app._panes[0]
        for i in range(N_MAX):
            pane._mount_block(Text(f"history {i}"), f"history {i}")
        await pilot.pause()
        pane._stick_to_bottom = False
        pane._transcript().scroll_to(y=10, animate=False)
        await pilot.pause()
        for i in range(200):
            pane._mount_block(Text(f"live {i}"), f"live {i}")
        await pilot.pause()

        span = pane._window_end - pane._window_start
        assert span == len(pane._mounted_blocks)
        assert pane._window_end <= len(pane._history)
        first = pane._history[pane._window_start]
        assert pane._mounted_blocks[0].text_payload() == first.payload


@pytest.mark.asyncio
async def test_returning_to_the_bottom_shows_the_newest_block():
    """Whatever the window dropped, scrolling back to the end must show
    the latest content — otherwise the transcript silently lies."""
    app = _app()
    async with app.run_test(size=(120, 40)) as pilot:
        pane = app._panes[0]
        for i in range(N_MAX):
            pane._mount_block(Text(f"history {i}"), f"history {i}")
        await pilot.pause()
        pane._stick_to_bottom = False
        pane._transcript().scroll_to(y=10, animate=False)
        await pilot.pause()
        for i in range(400):
            pane._mount_block(Text(f"live {i}"), f"live {i}")
        await pilot.pause()

        pane.jump_to_end()
        await pilot.pause()
        await pilot.pause()

        assert pane._window_end == len(pane._history)
        assert pane._mounted_blocks[-1].text_payload() == "live 399"
        assert len(pane._mounted_blocks) <= CEILING


@pytest.mark.asyncio
async def test_sticky_appends_still_evict_from_the_top():
    """The ordinary case is unchanged: pinned at the bottom, the window
    is bounded by dropping the oldest."""
    app = _app()
    async with app.run_test(size=(120, 40)) as pilot:
        pane = app._panes[0]
        for i in range(N_MAX + EVICT_BATCH + 10):
            pane._mount_block(Text(f"line {i}"), f"line {i}")
        await pilot.pause()

        assert pane._stick_to_bottom is True
        assert len(pane._mounted_blocks) <= CEILING
        assert pane._window_end == len(pane._history)
        assert pane._mounted_blocks[-1].text_payload() == \
            pane._history[-1].payload


@pytest.mark.asyncio
async def test_scrolling_back_down_restores_the_dropped_tail():
    """Reaching the bottom of a truncated window must not leave the user
    looking at stale content with no way to see the newest blocks."""
    app = _app()
    async with app.run_test(size=(120, 40)) as pilot:
        pane = app._panes[0]
        for i in range(N_MAX):
            pane._mount_block(Text(f"history {i}"), f"history {i}")
        await pilot.pause()
        pane._stick_to_bottom = False
        pane._transcript().scroll_to(y=10, animate=False)
        await pilot.pause()
        for i in range(400):
            pane._mount_block(Text(f"live {i}"), f"live {i}")
        await pilot.pause()
        assert pane._window_end < len(pane._history), "tail was not truncated"

        # The user scrolls back to the bottom of what is mounted.
        t = pane._transcript()
        t.scroll_to(y=t.max_scroll_y, animate=False)
        await pilot.pause()
        for _ in range(6):          # let the debounced restore land
            await pilot.pause()

        assert pane._window_end == len(pane._history)
        assert pane._mounted_blocks[-1].text_payload() == "live 399"
        assert len(pane._mounted_blocks) <= CEILING
