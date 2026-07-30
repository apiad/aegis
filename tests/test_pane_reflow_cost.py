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
