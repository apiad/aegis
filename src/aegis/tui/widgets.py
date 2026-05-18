from __future__ import annotations

from textual.widgets import Static

from aegis.tui.state import AgentState


class TabStrip(Static):
    """One row: the active agent's dot + name. Tab-ready (one entry in v1)."""

    def __init__(self, agent_name: str) -> None:
        super().__init__(markup=True)
        self._name = agent_name
        self._state = AgentState.ready

    def on_mount(self) -> None:
        self._refresh()

    def set_state(self, state: AgentState) -> None:
        self._state = state
        self._refresh()

    def _refresh(self) -> None:
        self.update(f"{self._state.dot} {self._name}")


class StatusBar(Static):
    """`<agent> · <model> · <permission>`, state label, then metrics."""

    def __init__(self, agent_name: str, model: str, permission: str) -> None:
        super().__init__(markup=True)
        self._identity = f"{agent_name} · {model} · {permission}"
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
