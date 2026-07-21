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

    def render_tab(self, idx, handle, slug, state, unseen, active,
                   suffix, colors) -> None:
        mark = "[bold]*[/bold]" if unseen else ""
        sfx = f" [{colors.muted}]{suffix}[/]" if suffix else ""
        label = (f"{state.dot(colors)} {idx} {handle} "
                 f"[{colors.accent}]·{slug}·[/]{sfx}{mark}")
        self.update(f"[reverse] {label} [/reverse]" if active
                    else f" {label} ")


class TabBar(HorizontalScroll):
    """Sideways-scrolling tab bar; the active tab is kept in view."""

    DEFAULT_CSS = """
    TabBar { height: 1; overflow-x: auto; overflow-y: hidden;
             scrollbar-size: 0 0; padding: 0 1; }
    TabBar > _TabCell { width: auto; height: 1; margin: 0 1; }
    """

    def __init__(self) -> None:
        super().__init__()
        self._cells: list[_TabCell] = []
        self._palette = aegis_colors(INK)
        self._items: list = []

    def set_palette(self, palette) -> None:
        self._palette = palette
        if self._cells:
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
        for cell, item in zip(self._cells, self._items):
            cell.render_tab(*item, self._palette)
            if item[5]:
                active_cell = cell
        if active_cell is not None:
            self.call_after_refresh(
                lambda c=active_cell: c.scroll_visible(animate=False))

    def bar_text(self) -> str:
        """Combined rendered text of all tab cells (for tests/inspection)."""
        return " ".join(str(c.content) for c in self._cells)


class StatusBar(Static):
    """`<agent> · <model> · <permission>`, state label, then metrics."""

    def __init__(self, model: str, effort: str, colors) -> None:
        super().__init__(markup=True)
        # Identity is just model · effort — the session name/handle already
        # lives in the tab bar, so repeating it here is noise.
        # Palette is captured once here. Runtime re-theming is a non-goal
        # (single theme); a future switch would need a set_palette that
        # rebuilds _identity (cf. pane/TabBar which do have set_palette).
        self._identity = f"{model}  [{colors.accent}]{effort}[/]"
        self._state = AgentState.ready
        self._metrics = ""
        self._system: str = ""
        self._connection_banner: str = ""
        self._plain_content: str = ""

    def on_mount(self) -> None:
        self._refresh()

    def set_state(self, state: AgentState) -> None:
        self._state = state
        self._refresh()

    def set_metrics(self, text: str) -> None:
        self._metrics = text
        self._refresh()

    def set_system(self, text: str) -> None:
        """System-stats segment (CPU/RAM/disk); empty string hides it."""
        self._system = text
        self._refresh()

    def set_connection_state(self, up: bool, reason: str = "") -> None:
        """Show/hide a disconnected indicator on the right of the bar.

        ``up=False`` renders ``⚠ disconnected — reconnecting…``; ``up=True``
        clears the indicator.  Suitable for wiring to WsClient.on_connection.
        """
        if up:
            self._connection_banner = ""
        else:
            self._connection_banner = "⚠ disconnected — reconnecting…"
        self._refresh()

    def render_plain(self) -> str:
        """Return the current bar content as a plain string (strips Rich markup).

        Used by tests to assert on visible text without a live Textual render.
        """
        import re
        raw = self._plain_content
        # Strip Rich markup tags like [bold], [red], [/], etc.
        return re.sub(r"\[[^\]]*\]", "", raw)

    def _refresh(self) -> None:
        import contextlib
        line = f"{self._identity}    {self._state.label}"
        if self._metrics:
            line += f"    {self._metrics}"
        if self._system:
            line += f"    {self._system}"
        if self._connection_banner:
            line += f"    {self._connection_banner}"
        # Keep a plain copy for render_plain() (no Textual dependency).
        self._plain_content = line
        with contextlib.suppress(Exception):
            self.update(line)
