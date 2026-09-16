"""FleetScreen — every session as a card, over the TUI, behind F10.

The screen draws what ``render_fleet`` draws and adds two things: a
selection, outlined in the accent colour, and the map from a clicked cell
back to a card. Both read the same layout ``render_fleet`` produced, so a
click lands on the card under the pointer and never on its neighbour.
"""

from __future__ import annotations

import time

from collections.abc import Callable
from dataclasses import replace

from rich.cells import cell_len
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from aegis.fleet.models import FleetSnapshot
from aegis.fleet.render import (
    CARD_WIDTH,
    GUTTER,
    columns_for,
    render_card,
    render_fleet,
)

# At most one redraw per this many seconds from the event stream. Nine
# sessions streaming at once would otherwise redraw hundreds of times a second.
COALESCE_S = 0.5

Rect = tuple[int, int, int, int]  # x, y, width, height, in cells


def in_tab_order(snapshot: FleetSnapshot, tabs: dict[str, int]) -> FleetSnapshot:
    """Number each live card by the tab bar and sort the grid by it.

    ``build_snapshot`` numbers cards by the brain's session list, which is
    not the tab bar: a terminal or file tab sits only in the bar, and a
    moved tab moves only there. ``tabs`` maps a handle to its 1-based tab.
    A session with no tab yet (its pane is still mounting) gets 0 and
    cannot be opened; ghosts keep their place at the end.
    """
    live = [c for c in snapshot.cards if c.ghost_since is None]
    ghosts = [c for c in snapshot.cards if c.ghost_since is not None]
    live = [replace(c, tab_index=tabs.get(c.handle, 0)) for c in live]
    live.sort(key=lambda c: (c.tab_index == 0, c.tab_index))
    return replace(snapshot, cards=tuple(live + ghosts))


def card_rects(snapshot: FleetSnapshot, pal, width: int) -> list[Rect]:
    """Where ``render_fleet`` put each card, in snapshot order.

    Mirrors its layout: the band's lines, a blank line, then rows of
    ``columns_for(width)`` cards padded to the row's tallest card, one blank
    line between rows. The band's height is read off the rendered text
    rather than recomputed, so it cannot drift from the renderer.
    """
    cards = snapshot.cards
    if not cards:
        return []
    cols = columns_for(width)
    card_w = min(CARD_WIDTH, width)
    rows = [cards[i : i + cols] for i in range(0, len(cards), cols)]
    heights = [
        max(len(render_card(c, pal, card_w).split("\n")) for c in row) for row in rows
    ]
    total = render_fleet(snapshot, pal, width).plain.count("\n") + 1
    y = total - sum(heights) - (len(rows) - 1)
    rects: list[Rect] = []
    for row, h in zip(rows, heights):
        for i, _card in enumerate(row):
            rects.append((i * (card_w + GUTTER), y, card_w, h))
        y += h + 1
    return rects


def _outline(text: Text, rect: Rect, style: str) -> None:
    """Restyle the border cells of ``rect`` in place. Offsets are cells, and
    a line can hold wide glyphs, so each is converted to a character index."""
    x, y, w, h = rect
    lines = text.plain.split("\n")
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line) + 1)

    def index(row: int, cell: int) -> int:
        used = 0
        for i, ch in enumerate(lines[row]):
            if used >= cell:
                return starts[row] + i
            used += cell_len(ch)
        return starts[row] + len(lines[row])

    for row in range(y, min(y + h, len(lines))):
        if row in (y, y + h - 1):
            text.stylize(style, index(row, x), index(row, x + w))
        else:
            text.stylize(style, index(row, x), index(row, x + 1))
            text.stylize(style, index(row, x + w - 1), index(row, x + w))


class _Grid(Static):
    def on_click(self, event) -> None:
        event.stop()
        self.screen.open_at(event.x, event.y)


class FleetScreen(ModalScreen):
    CSS = """
    FleetScreen { background: $background; }
    FleetScreen #fleet-scroll { width: 100%; height: 1fr; padding: 1 2 0 2; }
    FleetScreen #fleet-footer { dock: bottom; height: 1;
                                color: $foreground 60%; padding: 0 2; }
    """
    BINDINGS = [
        Binding("left", "move(-1)", "Left", priority=True),
        Binding("right", "move(1)", "Right", priority=True),
        Binding("up", "move_row(-1)", "Up", priority=True),
        Binding("down", "move_row(1)", "Down", priority=True),
        Binding("enter", "open", "Open", priority=True),
        *[Binding(str(n), f"pick({n})", show=False) for n in range(1, 10)],
    ]

    def __init__(self, snap: Callable[..., FleetSnapshot]) -> None:
        super().__init__()
        self._snap = snap
        self.selected = 1  # 1-based, a position in the grid
        self.chosen: int | None = None
        self._current: FleetSnapshot | None = None
        self._rects: list[Rect] = []
        self._cols = 1
        self._last_draw = 0.0
        self._pending = None

    # --- the members that hold no Textual call ---

    def _view(self) -> FleetSnapshot:
        return self._current if self._current is not None else self._snap()

    def action_move(self, delta: int) -> None:
        """Clamped, never wrapped: a grid you can fall off the end of is a
        grid where the arrow key does something different each press."""
        n = len(self._view().cards)
        if n:
            self.selected = max(1, min(n, self.selected + delta))
            self._draw()

    def action_move_row(self, delta: int) -> None:
        self.action_move(delta * self._cols)

    def action_pick(self, n: int) -> None:
        """``n`` is the tab number the card shows. It equals the card's place
        in the grid until a terminal or file tab sits between two sessions."""
        for i, card in enumerate(self._view().cards, start=1):
            if card.tab_index == n and card.ghost_since is None:
                self.selected = i
                self.action_open()
                return

    def action_open(self) -> None:
        cards = self._view().cards
        card = cards[self.selected - 1] if 1 <= self.selected <= len(cards) else None
        # A ghost is a dead ephemeral session: there is no tab to switch to.
        if card is None or card.ghost_since is not None or not card.tab_index:
            return
        self.chosen = card.tab_index
        if self.is_attached:
            self.dismiss(card.tab_index)

    def card_at(self, x: int, y: int) -> int | None:
        """The 1-based grid position of the card drawn at cell ``(x, y)`` of
        the grid, or ``None`` over the band, a gutter or a blank line."""
        for i, (rx, ry, w, h) in enumerate(self._rects, start=1):
            if rx <= x < rx + w and ry <= y < ry + h:
                return i
        return None

    def open_at(self, x: int, y: int) -> None:
        n = self.card_at(x, y)
        if n is not None:
            self.selected = n
            self.action_open()

    # --- mounted ---

    def compose(self) -> ComposeResult:
        self._grid = _Grid("", id="fleet-grid")
        with VerticalScroll(id="fleet-scroll"):
            yield self._grid
        yield Static(
            "←↑↓→ select  enter/click open tab  1-9 tab  esc/F10 close",
            id="fleet-footer",
        )

    def on_mount(self) -> None:
        self.set_interval(1.0, self.refresh_fleet)
        self.call_after_refresh(self.refresh_fleet)

    def on_resize(self, _event) -> None:
        self._draw()

    def poke(self) -> None:
        """An event arrived. Redraw now if the last redraw is old enough,
        otherwise once when the window closes; never more often."""
        if self._pending is not None or not self.is_attached:
            return
        wait = COALESCE_S - (time.monotonic() - self._last_draw)
        if wait <= 0:
            self.refresh_fleet()
        else:
            self._pending = self.set_timer(wait, self.refresh_fleet)

    def refresh_fleet(self) -> None:
        if self._pending is not None:
            self._pending.stop()
            self._pending = None
        self._last_draw = time.monotonic()
        snap = self._snap()
        # The one place wall-clock enters the dashboard, as a finished string.
        self._current = replace(
            snap, band=replace(snap.band, clock=time.strftime("%H:%M"))
        )
        self._draw()

    def _draw(self) -> None:
        if not self.is_attached or self._current is None:
            return
        snap = self._current
        n = len(snap.cards)
        self.selected = max(1, min(n, self.selected)) if n else 1
        pal = self.app.palette
        width = max(1, self._grid.content_size.width or self.app.size.width - 4)
        self._cols = columns_for(width)
        text = render_fleet(snap, pal, width)
        self._rects = card_rects(snap, pal, width)
        if self._rects:
            _outline(text, self._rects[self.selected - 1], f"bold {pal.accent}")
        self._grid.update(text)
