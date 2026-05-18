from __future__ import annotations

from enum import Enum


class AgentState(Enum):
    ready = "ready"
    working = "working"
    error = "error"

    def dot(self, colors) -> str:
        # Rich markup; color comes from the active theme's AegisColors.
        return {
            AgentState.ready: f"[{colors.ready}]●[/]",
            AgentState.working: f"[{colors.working}]●[/]",
            AgentState.error: f"[{colors.error}]●[/]",
        }[self]

    @property
    def label(self) -> str:
        return {
            AgentState.ready: "idle",
            AgentState.working: "✻ working…",
            AgentState.error: "⚠ error",
        }[self]
