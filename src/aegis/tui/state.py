from __future__ import annotations

from enum import Enum


class AgentState(Enum):
    ready = "ready"
    working = "working"
    error = "error"

    @property
    def dot(self) -> str:
        # Rich markup; rendered by Static widgets with markup enabled.
        return {
            AgentState.ready: "[green]●[/green]",
            AgentState.working: "[orange1]●[/orange1]",
            AgentState.error: "[red]●[/red]",
        }[self]

    @property
    def label(self) -> str:
        return {
            AgentState.ready: "idle",
            AgentState.working: "✻ working…",
            AgentState.error: "⚠ error",
        }[self]
