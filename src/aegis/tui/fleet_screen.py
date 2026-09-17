"""FleetScreen — every session in a list, over the TUI, behind F10.

The screen composes a band, a list and a detail. The renderers are pure and
take a frame number; two clocks drive them, the 1 s snapshot and the 0.5 s
frame. A rotator chooses the detail while nobody drives the screen.
"""

from __future__ import annotations

import time

from collections.abc import Callable, Iterable
from dataclasses import replace
from typing import TYPE_CHECKING, cast

from rich.cells import cell_len
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from aegis.fleet.ghosts import GhostBook
from aegis.fleet.models import FleetSnapshot
from aegis.fleet.render import render_band, render_detail, render_item
from aegis.fleet.rotation import Rotator

if TYPE_CHECKING:
    from aegis.tui.app import AegisApp

# At most one redraw per this many seconds from the event stream. Nine
# sessions streaming at once would otherwise redraw hundreds of times a second.
COALESCE_S = 0.5
# Below this many columns the detail stacks under the list.
NARROW = 110
_KEYS = "↑↓ select   enter open tab   1-9 tab   esc/F10 close"


def in_tab_order(snapshot: FleetSnapshot, tabs: dict[str, int]) -> FleetSnapshot:
    """Number each live session by the tab bar and sort the list by it.

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


class _Item(Static):
    def __init__(self, handle: str) -> None:
        super().__init__("")
        self.handle = handle

    def on_click(self, event) -> None:
        event.stop()
        screen = self.screen
        if isinstance(screen, FleetScreen):
            screen.click_item(self.handle)


class FleetScreen(ModalScreen):
    CSS = """
    FleetScreen { background: $background; }
    FleetScreen #fleet-band { height: auto; padding: 1 2 0 2; }
    FleetScreen #fleet-body { height: 1fr; padding: 1 2 0 2; }
    FleetScreen #fleet-list { width: 44%; height: 100%; margin-right: 1; }
    FleetScreen #fleet-detail { width: 1fr; height: 1fr;
                                border: round $accent; padding: 0 1; }
    FleetScreen #fleet-detail-body { height: auto; }
    FleetScreen _Item { border: round $panel-lighten-2; padding: 0 1;
                        margin-bottom: 1; height: auto; }
    FleetScreen _Item.-selected { border: round $accent; background: $boost; }
    FleetScreen _Item.-ghost { border: dashed $panel-lighten-2; }
    FleetScreen.-narrow #fleet-body { layout: vertical; }
    FleetScreen.-narrow #fleet-list { width: 100%; height: auto; max-height: 50%;
                                      margin-right: 0; }
    FleetScreen #fleet-footer { dock: bottom; height: 1;
                                color: $foreground 60%; padding: 0 2; }
    """
    BINDINGS = [
        Binding("up", "move(-1)", "Up", priority=True),
        Binding("down", "move(1)", "Down", priority=True),
        Binding("enter", "open", "Open", priority=True),
        *[Binding(str(n), f"pick({n})", show=False) for n in range(1, 10)],
    ]

    def __init__(
        self,
        snap: Callable[..., FleetSnapshot],
        sessions: Callable[[], Iterable] = tuple,
        system_row: Callable[[], dict] = dict,
    ) -> None:
        super().__init__()
        self._snap = snap
        self._sessions = sessions
        # F3's SYSTEM row, which F10 hides: the app's system, quota and build
        # tiers from its last tick, so both views show the same numbers
        # whatever tab sits behind the modal.
        self._system_row = system_row
        # Every session carrying this screen's event observer, so unmount can
        # take each one off again: a session outlives any one screen.
        self._hooked: list = []
        self.selected = 1  # 1-based, a position in snapshot.cards
        self.frame = 0
        self.rotator = Rotator()
        # The handle of the card opened; the app resolves it to a tab then.
        self.chosen: str | None = None
        self._current: FleetSnapshot | None = None
        self._items: dict[str, _Item] = {}
        self._drawn: str | None = None  # the handle the last draw selected
        self._last_draw = 0.0
        self._pending = None
        # One book per screen: a ghost is a viewing artefact, and a client
        # that was not open has nothing to catch up on.
        self._ghosts = GhostBook()

    # --- the members that hold no Textual call ---

    def _view(self) -> FleetSnapshot:
        return self._current if self._current is not None else self._snap()

    def _selected_handle(self) -> str | None:
        cards = self._view().cards
        return (
            cards[self.selected - 1].handle
            if 1 <= self.selected <= len(cards)
            else None
        )

    def _operator_chose(self) -> None:
        """A key or click: auto mode stops for its idle time, and the dwell
        and countdown restart from what is now on screen."""
        now = time.monotonic()
        self.rotator.touch(now)
        if (handle := self._selected_handle()) is not None:
            self.rotator.show(handle, now)

    def action_move(self, delta: int) -> None:
        """Clamped, never wrapped: a list you can fall off the end of is a
        list where the arrow key does something different each press."""
        n = len(self._view().cards)
        if n:
            self.selected = max(1, min(n, self.selected + delta))
        self._operator_chose()
        self._draw()

    def select_handle(self, handle: str) -> None:
        for i, card in enumerate(self._view().cards, start=1):
            if card.handle == handle:
                self.selected = i
                return

    def click_item(self, handle: str) -> None:
        """The first click on an item selects it; a click on the item the
        operator selected opens it. An item auto mode highlighted is not the
        operator's choice, so a click on it only selects."""
        chosen_by_operator = not self.rotator.is_auto(time.monotonic())
        if handle == self._selected_handle() and chosen_by_operator:
            self.action_open()
            return
        self.select_handle(handle)
        self._operator_chose()
        self._draw()

    def action_pick(self, n: int) -> None:
        """``n`` is the tab number the item shows. It equals the item's place
        in the list until a terminal or file tab sits between two sessions."""
        for i, card in enumerate(self._view().cards, start=1):
            if card.tab_index == n and card.ghost_since is None:
                self.selected = i
                self.action_open()
                return

    def action_open(self) -> None:
        cards = self._view().cards
        card = cards[self.selected - 1] if 1 <= self.selected <= len(cards) else None
        # A ghost is a dead ephemeral session: there is no tab to switch to.
        if card is None or card.ghost_since is not None:
            return
        # The handle, never the tab number: the list can be a second old, and
        # a tab closed or moved since would turn the number into a neighbour.
        self.chosen = card.handle
        if self.is_attached:
            self.dismiss(card.handle)

    def advance_frame(self) -> None:
        """The motion clock: redraw from the snapshot held, never a new one."""
        self.frame += 1
        self._draw()

    # --- mounted ---

    def compose(self) -> ComposeResult:
        self._band = Static("", id="fleet-band")
        yield self._band
        with Horizontal(id="fleet-body"):
            self._list = VerticalScroll(id="fleet-list")
            yield self._list
            with VerticalScroll(id="fleet-detail") as detail:
                self._detail = detail
                self._detail_body = Static("", id="fleet-detail-body")
                yield self._detail_body
        self._footer = Static(_KEYS, id="fleet-footer")
        yield self._footer

    def on_mount(self) -> None:
        self.set_interval(1.0, self.refresh_fleet)
        self.set_interval(0.5, self.advance_frame)
        self.call_after_refresh(self.refresh_fleet)

    def on_unmount(self) -> None:
        for session in self._hooked:
            self._unhook(session)
        self._hooked.clear()

    def _unhook(self, session) -> None:
        session.remove_event_observer(self._on_event)
        session.remove_fleet_watcher(self._on_fleet_recap)

    def on_resize(self, event) -> None:
        self.set_class(event.size.width < NARROW, "-narrow")
        self._draw()

    def _hook_events(self) -> None:
        """Observe sessions not seen yet. One spawned while the screen is
        open is picked up on the next refresh, at most a second later, and
        one that closed is let go of then: `aegis dash` stays up all day."""
        current = list(self._sessions())
        for session in [h for h in self._hooked if not any(h is s for s in current)]:
            self._unhook(session)
            self._hooked.remove(session)
        for session in current:
            if not any(session is h for h in self._hooked):
                self._hooked.append(session)
                session.add_event_observer(self._on_event)
                # F10 shows every item's `now` line, so it watches every
                # session, and pays for their recaps, while it is open.
                session.add_fleet_watcher(self._on_fleet_recap)

    def _on_event(self, _session, _ev) -> None:
        self.poke()

    def _on_fleet_recap(self, _session, _recap) -> None:
        self.poke()

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
        # Monotonic, the clock ghost_since and build_snapshot's ages share.
        now = self._last_draw = time.monotonic()
        if self.is_attached:
            self._hook_events()
        # The selection follows its session: a refresh that reorders the list
        # must not move the selection onto a different session.
        held = self._selected_handle() if self._current is not None else None
        snap = self._snap(now=now)
        # A ghost is only ever a live card seen on an earlier refresh, so the
        # book observes the live fleet and the builder draws its ghosts. The
        # second build runs only while a ghost is on screen.
        self._ghosts.observe(snap.cards, now)
        if ghosts := self._ghosts.alive(now):
            snap = self._snap(now=now, ghosts=ghosts)
        # The one place wall-clock enters the dashboard, as a finished string.
        band = replace(snap.band, clock=time.strftime("%H:%M"), **self._system_row())
        self._current = replace(snap, band=band)
        if held is not None:
            self.select_handle(held)
        # On the first refresh nothing has been on screen yet, so the rotator
        # is not told the default first item is its current one.
        if held not in (c.handle for c in self._current.cards):
            held = None
        self.rotator.observe(self._current, now)
        if self.rotator.is_auto(now):
            handle = self.rotator.pick(now, current=held)
            if handle is not None:
                self.select_handle(handle)
        self._draw()

    def _draw(self) -> None:
        if not self.is_attached or self._current is None:
            return
        snap = self._current
        cards = snap.cards
        n = len(cards)
        self.selected = max(1, min(n, self.selected)) if n else 1
        pal = cast("AegisApp", self.app).palette
        width = max(1, self._band.content_size.width or self.app.size.width - 4)
        self._band.update(render_band(snap, pal, width, self.frame))

        # Reconcile the items to the cards by handle: a widget per session
        # lives as long as the session, so its border and scroll stay put.
        handles = [c.handle for c in cards]
        for handle in [h for h in self._items if h not in handles]:
            self._items.pop(handle).remove()
        for handle in handles:
            if handle not in self._items:
                self._items[handle] = _Item(handle)
                self._list.mount(self._items[handle])
        live = [w for w in self._list.children if isinstance(w, _Item)]
        if [w.handle for w in live if w.handle in self._items] != handles:
            for i, handle in enumerate(handles):
                item = self._items[handle]
                if i == 0:
                    self._list.move_child(item, before=0)
                else:
                    self._list.move_child(item, after=self._items[handles[i - 1]])
        chosen = cards[self.selected - 1] if n else None
        for card in cards:
            item = self._items[card.handle]
            item.update(render_item(card, pal, self.frame))
            item.set_class(card is chosen, "-selected")
            item.set_class(card.ghost_since is not None, "-ghost")

        if chosen is None:
            self._detail_body.update(Text("no sessions", style=pal.muted))
        else:
            detail_w = max(1, self._detail_body.content_size.width or width // 2)
            self._detail_body.update(render_detail(chosen, pal, detail_w, self.frame))
        if chosen is not None and chosen.handle != self._drawn:
            item = self._items[chosen.handle]
            self.call_after_refresh(self._list.scroll_to_widget, item, animate=False)
            self._detail.scroll_home(animate=False)
        self._drawn = chosen.handle if chosen is not None else None

        footer = Text(_KEYS)
        remaining = self.rotator.countdown(time.monotonic())
        if remaining is not None:
            auto = f"auto · next in {remaining:.0f}s"
            room = self._footer.content_size.width or self.app.size.width - 4
            footer.append(" " * max(2, room - footer.cell_len - cell_len(auto)))
            footer.append(auto)
        self._footer.update(footer)
