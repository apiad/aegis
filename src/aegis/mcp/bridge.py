from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class SessionInfo:
    handle: str
    agent_slug: str
    state: str          # AgentState.value: "ready" | "working" | "error"
    active: bool
    unseen: bool


@runtime_checkable
class AppBridge(Protocol):
    def list_sessions(self) -> list[SessionInfo]: ...
    def list_agents(self) -> list[str]: ...
    async def handoff(self, from_handle: str, target_handle: str,
                      context: str) -> str: ...
