"""PendingStrip / Chip — the click-to-dequeue queue above the input box."""
from __future__ import annotations

import pytest
from textual.app import App, ComposeResult

from aegis.queue import InboxMessage, sender_user
from aegis.tui.pending import Chip, PendingStrip, chip_label
from aegis.tui.themes import INK, aegis_colors


def _user(body):
    return InboxMessage(sender=sender_user(),
                        timestamp="2026-06-26T00:00:00Z", body=body)


class StripApp(App):
    def __init__(self):
        super().__init__()
        self.dequeued = []

    def compose(self) -> ComposeResult:
        yield PendingStrip(aegis_colors(INK))

    def on_chip_dequeued(self, event: Chip.Dequeued) -> None:
        self.dequeued.append(event.msg)


def test_chip_label_collapses_and_truncates():
    assert chip_label("hello") == "hello"
    assert chip_label("a\nb\nc") == "a b c"
    long = chip_label("y" * 100)
    assert long.endswith("…") and len(long) <= 41


@pytest.mark.asyncio
async def test_strip_starts_empty_and_hidden():
    app = StripApp()
    async with app.run_test():
        strip = app.query_one(PendingStrip)
        assert strip.has_class("-empty")
        assert list(strip.chips) == []


@pytest.mark.asyncio
async def test_add_creates_chip_for_message():
    app = StripApp()
    async with app.run_test() as pilot:
        strip = app.query_one(PendingStrip)
        msg = _user("queued thing")
        strip.add(msg)
        await pilot.pause()
        chips = list(strip.chips)
        assert len(chips) == 1 and chips[0].msg is msg
        assert not strip.has_class("-empty")


@pytest.mark.asyncio
async def test_remove_msg_drops_chip_and_re_hides_when_empty():
    app = StripApp()
    async with app.run_test() as pilot:
        strip = app.query_one(PendingStrip)
        a, b = _user("a"), _user("b")
        strip.add(a)
        strip.add(b)
        await pilot.pause()
        strip.remove_msg(a)
        await pilot.pause()
        assert [c.msg for c in strip.chips] == [b]
        strip.remove_msg(b)
        await pilot.pause()
        assert list(strip.chips) == [] and strip.has_class("-empty")


@pytest.mark.asyncio
async def test_clicking_chip_posts_dequeued_with_its_message():
    app = StripApp()
    async with app.run_test() as pilot:
        strip = app.query_one(PendingStrip)
        msg = _user("click me")
        strip.add(msg)
        await pilot.pause()
        await pilot.click(Chip)
        await pilot.pause()
        assert app.dequeued == [msg]
