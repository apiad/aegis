"""PendingStrip — click-to-dequeue queue of user messages above the input.

When the agent is mid-turn, text typed into the box is buffered rather than
blocked. Each buffered user message shows here as a ``Chip``; clicking a chip
cancels that message before it ever reaches the agent. The strip drains
itself (via the pane) when the messages dispatch at the turn boundary.
"""
from __future__ import annotations

from textual.containers import HorizontalScroll
from textual.events import Click
from textual.message import Message
from textual.widgets import Static

from aegis.queue.schema import InboxMessage

CHIP_LIMIT = 40


def chip_label(body: str, limit: int = CHIP_LIMIT) -> str:
    """One-line label for a chip: collapse whitespace, truncate with ellipsis."""
    flat = " ".join(body.split())
    if len(flat) > limit:
        return flat[: limit - 1] + "…"
    return flat


class Chip(Static):
    """One queued user message. Click to dequeue (cancel)."""

    DEFAULT_CSS = """
    Chip { width: auto; height: 1; margin: 0 1 0 0; padding: 0 1;
           background: $surface; color: $foreground; }
    Chip:hover { background: $panel; }
    """

    class Dequeued(Message):
        def __init__(self, chip: "Chip", msg: InboxMessage) -> None:
            super().__init__()
            self.chip = chip
            self.msg = msg

    def __init__(self, msg: InboxMessage, palette) -> None:
        super().__init__(markup=False)
        self.msg = msg
        self._palette = palette
        self.tooltip = "click to dequeue"
        self.update(f"✕ {chip_label(msg.body)}")

    def on_click(self, event: Click) -> None:
        event.stop()
        self.post_message(self.Dequeued(self, self.msg))


class PendingStrip(HorizontalScroll):
    """Sideways-scrolling row of pending-user chips. Hidden when empty."""

    DEFAULT_CSS = """
    PendingStrip { height: 1; overflow-x: auto; overflow-y: hidden;
                   scrollbar-size: 0 0; padding: 0 2; margin-bottom: 1;
                   background: transparent; }
    PendingStrip.-empty { display: none; }
    """

    def __init__(self, palette) -> None:
        super().__init__(id="pending-strip")
        self._palette = palette
        self.add_class("-empty")

    @property
    def chips(self):
        """Chips in queue (DOM) order."""
        return list(self.query(Chip))

    def set_palette(self, palette) -> None:
        self._palette = palette

    def add(self, msg: InboxMessage) -> Chip:
        chip = Chip(msg, self._palette)
        self.mount(chip)
        self.remove_class("-empty")
        return chip

    def remove_msg(self, msg: InboxMessage) -> None:
        for chip in self.query(Chip):
            if chip.msg is msg:
                chip.remove()
                break
        # If that was the last chip, re-hide. query() still includes the
        # just-removed chip until the next layout tick, so count by identity.
        if all(c.msg is msg for c in self.query(Chip)):
            self.add_class("-empty")

    def clear(self) -> None:
        for chip in self.query(Chip):
            chip.remove()
        self.add_class("-empty")
