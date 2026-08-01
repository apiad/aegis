from __future__ import annotations

from textual import events
from textual.containers import HorizontalScroll
from textual.message import Message
from textual.widgets import Static, TextArea

from aegis.tui.state import AgentState
from aegis.tui.themes import aegis_colors, INK


class GrowingInput(TextArea):
    """Multi-line chat input that grows from 1 to ``MAX_LINES`` rows of
    content, then scrolls. Drops in where Textual's ``Input`` was used:
    exposes ``value`` and a ``Submitted`` message with ``.value``.

    ``enter`` submits with ``kind="enqueue"``; ``alt+enter`` / ``ctrl+enter``
    submit with ``kind="interrupt"`` (send-with-interrupt); ``shift+enter`` /
    ``ctrl+j`` insert a newline. ``alt+enter`` reliably distinguishes across
    terminals; ``ctrl+enter`` only does under the Kitty keyboard protocol, so
    it is a bonus alias.
    """

    MAX_LINES = 5

    class Submitted(Message):
        def __init__(self, sender: "GrowingInput", value: str,
                     kind: str = "enqueue") -> None:
            super().__init__()
            self.input = sender
            self.value = value
            self.kind = kind

        @property
        def control(self) -> "GrowingInput":
            return self.input

    def __init__(self, placeholder: str = "", *,
                 id: str | None = None) -> None:
        super().__init__(
            soft_wrap=True,
            show_line_numbers=False,
            tab_behavior="focus",
            placeholder=placeholder,
            id=id,
        )
        # Session-lifetime recall ring of sent messages (per widget = per pane).
        self._history: list[str] = []
        self._hist_idx: int | None = None
        self._hist_draft: str = ""
        # Optional key hook: returns True if it consumed the key (the command
        # palette uses this to grab Up/Down/Tab/Enter/Esc while it is open).
        self.key_interceptor = None

    @property
    def value(self) -> str:
        return self.text

    @value.setter
    def value(self, v: str) -> None:
        self.text = v
        self._resize_to_content()

    def on_mount(self) -> None:
        self._resize_to_content()

    def render_lines(self, crop):
        # Pruning a widget clears its component styles, but the compositor
        # can still render it one refresh tick later from a stale
        # arrangement — and TextArea.render_lines applies its theme through
        # those styles, so it KeyErrors on 'text-area--gutter' and panics
        # the app (closing a tab did exactly that). A pruned input has
        # nothing left to draw.
        if not self.is_attached:
            from textual.strip import Strip
            return [Strip.blank(crop.width)] * crop.height
        return super().render_lines(crop)

    def _resize_to_content(self) -> None:
        # Count visual rows, not just hard-newline lines — a long
        # single-line string that soft-wraps should still grow the
        # widget. wrapped_document.height needs a known wrap width; if
        # the widget hasn't laid out yet, fall back to line_count.
        try:
            rows = self.wrapped_document.height
        except Exception:
            rows = self.document.line_count
        n = max(1, min(self.MAX_LINES, rows))
        # +2 for the top + bottom border rows
        self.styles.height = n + 2

    def on_text_area_changed(self, _event: TextArea.Changed) -> None:
        self._resize_to_content()

    def on_resize(self, _event) -> None:
        # When the viewport width changes, soft-wrap recomputes and
        # the row count can shift even though the text didn't change.
        self._resize_to_content()

    async def action_submit(self, kind: str = "enqueue") -> None:
        self._record_history(self.text)
        self.post_message(self.Submitted(self, self.text, kind))

    def _record_history(self, text: str) -> None:
        text = text.strip()
        if text and (not self._history or self._history[-1] != text):
            self._history.append(text)
        self._hist_idx = None
        self._hist_draft = ""

    def _history_prev(self) -> None:
        if not self._history:
            return
        if self._hist_idx is None:
            self._hist_draft = self.text
            self._hist_idx = len(self._history) - 1
        elif self._hist_idx > 0:
            self._hist_idx -= 1
        else:
            return
        self.value = self._history[self._hist_idx]
        self.move_cursor(self.document.end)

    def _history_next(self) -> None:
        if self._hist_idx is None:
            return
        if self._hist_idx < len(self._history) - 1:
            self._hist_idx += 1
            self.value = self._history[self._hist_idx]
        else:
            self._hist_idx = None
            self.value = self._hist_draft
            self._hist_draft = ""
        self.move_cursor(self.document.end)

    async def _on_key(self, event: events.Key) -> None:
        if self.key_interceptor is not None and self.key_interceptor(event):
            event.stop()
            event.prevent_default()
            return
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            await self.action_submit("enqueue")
            return
        if event.key in ("alt+enter", "ctrl+enter"):
            event.stop()
            event.prevent_default()
            await self.action_submit("interrupt")
            return
        if event.key in ("shift+enter", "ctrl+j"):
            event.stop()
            event.prevent_default()
            start, end = self.selection
            self._replace_via_keyboard("\n", start, end)
            return
        if event.key == "up" and self.cursor_at_first_line and self._history:
            event.stop()
            event.prevent_default()
            self._history_prev()
            return
        if event.key == "down" and self.cursor_at_last_line \
                and self._hist_idx is not None:
            event.stop()
            event.prevent_default()
            self._history_next()
            return
        await super()._on_key(event)


class _TabCell(Static):
    """One tab in the bar; width sizes to its content so the row overflows."""

    # Dragging a tab across the bar would otherwise start a text selection.
    ALLOW_SELECT = False

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        # 0-based pane index this cell stands for; -1 until it renders a tab
        # (the "no tabs" placeholder keeps it out of range, so a click on it
        # falls through the app's bounds check as a no-op).
        self._index = -1

    def render_tab(self, idx, handle, slug, state, unseen, active,
                   suffix, colors) -> None:
        self._index = idx - 1
        mark = "[bold]*[/bold]" if unseen else ""
        sfx = f" [{colors.muted}]{suffix}[/]" if suffix else ""
        label = (f"{state.dot(colors)} {idx} {handle} "
                 f"[{colors.accent}]·{slug}·[/]{sfx}{mark}")
        self.update(f"[reverse] {label} [/reverse]" if active
                    else f" {label} ")

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.post_message(TabBar.Selected(self._index))

    # --- drag-to-reorder ------------------------------------------------
    # No mouse capture: cells are positional (cell i always paints tab i),
    # so the cell the pointer is over already *is* the drop target, and
    # letting Textual route each MouseMove to the widget under the pointer
    # does the hit-testing for us. The cost of not capturing is that a
    # release outside the bar leaves a stale origin behind — harmless,
    # because a move with no button held clears it before it can act.

    def _bar(self) -> "TabBar | None":
        return self.parent if isinstance(self.parent, TabBar) else None

    def on_mouse_down(self, event: events.MouseDown) -> None:
        bar = self._bar()
        if bar is not None and self._index >= 0:
            bar.begin_drag(self._index)

    def on_mouse_move(self, event: events.MouseMove) -> None:
        bar = self._bar()
        if bar is None:
            return
        if not event.button:            # a plain hover, not a drag
            bar.cancel_drag()
            return
        if self._index >= 0:
            bar.drag_over(self._index)

    def on_mouse_up(self, event: events.MouseUp) -> None:
        bar = self._bar()
        if bar is not None:
            bar.cancel_drag()


class TabBar(HorizontalScroll):
    """Sideways-scrolling tab bar; the active tab is kept in view."""

    class Selected(Message):
        """A tab was clicked. ``index`` is the 0-based pane index."""

        def __init__(self, index: int) -> None:
            super().__init__()
            self.index = index

    class Reordered(Message):
        """A tab was dragged one or more slots. Both indices are 0-based
        pane indices, and the move is "pop ``from_index``, insert at
        ``to_index``"."""

        def __init__(self, from_index: int, to_index: int) -> None:
            super().__init__()
            self.from_index = from_index
            self.to_index = to_index

    DEFAULT_CSS = """
    TabBar { height: 1; overflow-x: auto; overflow-y: hidden;
             scrollbar-size: 0 0; padding: 0 1; }
    TabBar > _TabCell { width: auto; height: 1; margin: 0 1; }
    TabBar > _TabCell:hover { text-style: underline; }
    """

    def __init__(self) -> None:
        super().__init__()
        self._cells: list[_TabCell] = []
        self._palette = aegis_colors(INK)
        self._items: list = []
        # What each cell was last painted with. Any pane's state change
        # refreshes the whole bar, and repainting every cell made that
        # O(tabs) — 40 panes flipping together cost over a second of frozen
        # UI, all of it re-rendering labels that hadn't changed.
        self._painted: list = []
        # Index the in-flight drag currently sits at, or None when no
        # button is down on a tab. It tracks the *live* slot, not the
        # origin, so a drag across several cells emits one hop per cell.
        self._drag_from: int | None = None

    def begin_drag(self, index: int) -> None:
        self._drag_from = index

    def cancel_drag(self) -> None:
        self._drag_from = None

    def drag_over(self, index: int) -> None:
        """The pointer entered cell ``index`` mid-drag: hop the tab there."""
        frm = self._drag_from
        if frm is None or frm == index or not (0 <= index < len(self._items)):
            return
        self._drag_from = index
        self.post_message(self.Reordered(frm, index))

    def set_palette(self, palette) -> None:
        self._palette = palette
        if self._cells:
            self._painted = []      # colors aren't in the item tuple
            self._refresh_cells()

    def set_tabs(self, items: list) -> None:
        if not items:
            items = [(0, "no tabs", "", AgentState.ready, False, False, None)]
        self._items = items
        while len(self._cells) < len(items):
            cell = _TabCell(markup=True)
            self._cells.append(cell)
            self.mount(cell)
        while len(self._cells) > len(items):
            self._cells.pop().remove()
        self._refresh_cells()

    def _refresh_cells(self) -> None:
        active_cell = None
        painted = list(self._painted)
        painted += [None] * (len(self._items) - len(painted))
        for i, (cell, item) in enumerate(zip(self._cells, self._items)):
            if painted[i] != item:
                cell.render_tab(*item, self._palette)
                painted[i] = item
            if item[5]:
                active_cell = cell
        self._painted = painted[:len(self._items)]
        if active_cell is not None:
            self.call_after_refresh(
                lambda c=active_cell: c.scroll_visible(animate=False))

    def bar_text(self) -> str:
        """Combined rendered text of all tab cells (for tests/inspection)."""
        return " ".join(str(c.content) for c in self._cells)


def short_model(name: str) -> str:
    """``claude-opus-4-8`` -> ``opus-4.8``; unrecognised shapes pass through."""
    import re
    return re.sub(r"-(\d+)-(\d+)$", r"-\1.\2", name.removeprefix("claude-"))


class StatusBar(Static):
    """`<agent> · <model> · <permission>`, state label, then metrics.

    Segments are composed through ``aegis.tui.fit``: each carries a tuple of
    progressively narrower forms plus a priority, and the bar degrades from the
    bottom of the priority order until it fits the terminal. Static segments
    (identity, system stats) rank lowest deliberately — on a narrow terminal you
    should lose what never changes and keep what does.
    """

    # Priority ladder — higher survives longer. Ranked by "does this change,
    # and does it demand action", not by how interesting it is.
    P_CONNECTION, P_STATE, P_LOOP = 70, 60, 50
    P_QUOTA, P_METRICS, P_IDENTITY, P_SYSTEM = 40, 30, 20, 10

    def __init__(self, model: str, effort: str, colors) -> None:
        super().__init__(markup=True)
        # Identity is just model · effort — the session name/handle already
        # lives in the tab bar, so repeating it here is noise.
        # Palette is captured once here. Runtime re-theming is a non-goal
        # (single theme); a future switch would need a set_palette that
        # rebuilds _identity (cf. pane/TabBar which do have set_palette).
        from aegis.version import BUILD
        eff = f"[{colors.accent}]{effort}[/]"
        self._identity: tuple[str, ...] = (
            f"[dim]aegis {BUILD}[/]  {model}  {eff}",
            f"{short_model(model)} {eff}",
            short_model(model),
        )
        self._state = AgentState.ready
        self._metrics: tuple[str, ...] = ()
        self._system: tuple[str, ...] = ()
        self._loop: tuple[str, ...] = ()
        self._quota: tuple[str, ...] = ()
        self._connection: tuple[str, ...] = ()
        self._plain_content: str = ""
        # Tests set this to exercise narrow widths without a live layout.
        self._width_override: int | None = None

    @staticmethod
    def _tiers(value) -> tuple[str, ...]:
        """Normalise a setter argument to a tier tuple."""
        if value is None:
            return ()
        if isinstance(value, str):
            return (value,) if value else ()
        return tuple(t for t in value if t)

    def on_mount(self) -> None:
        self._refresh()

    def on_resize(self) -> None:
        self._refresh()

    def set_state(self, state: AgentState) -> None:
        self._state = state
        self._refresh()

    def set_metrics(self, text) -> None:
        self._metrics = self._tiers(text)
        self._refresh()

    def set_system(self, text) -> None:
        """System-stats segment (CPU/RAM/disk); empty hides it."""
        self._system = self._tiers(text)
        self._refresh()

    def set_quota(self, text) -> None:
        """Claude subscription-quota segment; empty hides it."""
        self._quota = self._tiers(text)
        self._refresh()

    def set_loop(self, status: dict | None) -> None:
        """Loop segment (``⟳ loop 3/20``); None hides it."""
        self._loop = () if status is None else (
            f"⟳ loop {status['iteration']}/{status['max_iterations']}",
            f"⟳{status['iteration']}/{status['max_iterations']}",
        )
        self._refresh()

    def set_connection_state(self, up: bool, reason: str = "") -> None:
        """Show/hide a disconnected indicator on the right of the bar.

        ``up=False`` renders ``⚠ disconnected — reconnecting…``; ``up=True``
        clears the indicator.  Suitable for wiring to WsClient.on_connection.
        """
        self._connection = () if up else (
            "⚠ disconnected — reconnecting…", "⚠ disconnected")
        self._refresh()

    def render_plain(self) -> str:
        """Return the current bar content as a plain string (strips Rich markup).

        Used by tests to assert on visible text without a live Textual render.
        """
        from aegis.tui.fit import strip_markup
        return strip_markup(self._plain_content)

    def _available_width(self) -> int:
        if self._width_override is not None:
            return self._width_override
        try:
            return int(self.size.width)
        except Exception:  # noqa: BLE001 — not laid out yet
            return 0

    def _refresh(self) -> None:
        import contextlib

        from aegis.tui.fit import Segment, fit
        # List order is visual order; priority is independent of it.
        segments = [
            Segment("identity", self._identity, self.P_IDENTITY),
            Segment("state", (self._state.label,), self.P_STATE),
            Segment("metrics", self._metrics, self.P_METRICS),
            Segment("loop", self._loop, self.P_LOOP),
            Segment("quota", self._quota, self.P_QUOTA),
            Segment("system", self._system, self.P_SYSTEM),
            Segment("connection", self._connection, self.P_CONNECTION),
        ]
        line = fit(segments, self._available_width())
        # Keep a plain copy for render_plain() (no Textual dependency).
        self._plain_content = line
        with contextlib.suppress(Exception):
            # layout=False: the bar is `height: 1` and `fit()` has already
            # trimmed the line to the available width, so its size cannot
            # change. The default (layout=True) made every metrics update
            # rebuild the whole compositor map — one full-screen reflow per
            # streamed delta, which swamped every other repaint economy.
            self.update(line, layout=False)
