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

    Plain ``enter`` submits; ``shift+enter`` / ``ctrl+j`` / ``alt+enter``
    insert a newline (terminals vary on which one they emit, so all three
    are wired).
    """

    MAX_LINES = 5

    class Submitted(Message):
        def __init__(self, sender: "GrowingInput", value: str) -> None:
            super().__init__()
            self.input = sender
            self.value = value

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
        n = max(1, min(self.MAX_LINES, self.document.line_count))
        # +2 for the top + bottom border rows
        self.styles.height = n + 2

    def on_text_area_changed(self, _event: TextArea.Changed) -> None:
        self._resize_to_content()

    async def action_submit(self) -> None:
        self.post_message(self.Submitted(self, self.text))

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            await self.action_submit()
            return
        if event.key in ("shift+enter", "ctrl+j", "alt+enter"):
            event.stop()
            event.prevent_default()
            start, end = self.selection
            self._replace_via_keyboard("\n", start, end)
            return
        await super()._on_key(event)


class _TabCell(Static):
    """One tab in the bar; width sizes to its content so the row overflows."""

    def render_tab(self, idx, handle, slug, state, unseen, active,
                   colors) -> None:
        mark = "[bold]*[/bold]" if unseen else ""
        label = (f"{state.dot(colors)} {idx} {handle} "
                 f"[{colors.accent}]·{slug}·[/]{mark}")
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
            items = [(0, "no tabs", "", AgentState.ready, False, False)]
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

    def __init__(self, handle: str, agent_slug: str,
                 model: str, permission: str, colors) -> None:
        super().__init__(markup=True)
        # Palette is captured once here. Runtime re-theming is a non-goal
        # (single theme); a future switch would need a set_palette that
        # rebuilds _identity (cf. pane/TabBar which do have set_palette).
        self._identity = (
            f"{handle}  [{colors.accent}]·{agent_slug}·[/]  "
            f"{model} · {permission}")
        self._state = AgentState.ready
        self._metrics = ""

    def on_mount(self) -> None:
        self._refresh()

    def set_state(self, state: AgentState) -> None:
        self._state = state
        self._refresh()

    def set_metrics(self, text: str) -> None:
        self._metrics = text
        self._refresh()

    def _refresh(self) -> None:
        line = f"{self._identity}    {self._state.label}"
        if self._metrics:
            line += f"    {self._metrics}"
        self.update(line)
