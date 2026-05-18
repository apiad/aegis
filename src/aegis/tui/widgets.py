from __future__ import annotations

from textual.widgets import Static

from aegis.tui.state import AgentState


class TabBar(Static):
    """One-line tab bar: [dot idx handle ·slug·]… active reversed, * unseen."""

    def __init__(self) -> None:
        super().__init__(markup=True)
        self._items: list = []

    def set_tabs(self, items: list) -> None:
        self._items = items
        self._refresh()

    def _refresh(self) -> None:
        cells = []
        for idx, handle, slug, state, unseen, active in self._items:
            mark = "[bold]*[/bold]" if unseen else ""
            label = (f"{state.dot} {idx} {handle} "
                     f"[#788C5D]·{slug}·[/#788C5D]{mark}")
            cells.append(f"[reverse] {label} [/reverse]"
                         if active else f" {label} ")
        self.update("".join(cells) or "no tabs")


class StatusBar(Static):
    """`<agent> · <model> · <permission>`, state label, then metrics."""

    def __init__(self, handle: str, agent_slug: str,
                 model: str, permission: str) -> None:
        super().__init__(markup=True)
        self._identity = (
            f"{handle}  [#788C5D]·{agent_slug}·[/#788C5D]  "
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
