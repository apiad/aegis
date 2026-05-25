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


class GroupsBridge(Protocol):
    """Concrete surface for the aegis_group_* MCP tools."""

    async def spawn(self, *, profile: str, group: str,
                    handle: str | None = None) -> str: ...
    async def broadcast(self, group: str, *, sender: str,
                        objective: str, output_format: str,
                        tool_guidance: str, boundaries: str) -> str: ...
    async def wait_all(self, group: str, *, timeout: float = 600.0,
                       reducer: str = "concat"): ...
    async def wait_any(self, group: str, *, timeout: float = 600.0,
                       cancel_losers: bool = True): ...
    async def spawn_mixed(self, *, group: str,
                          profiles: list[str]) -> list[str]: ...


@runtime_checkable
class AppBridge(Protocol):
    """Surface the MCP server consumes. Implementors today:
    ``SessionManager`` (headless / serve) and ``AegisApp`` (TUI). Both
    expose ``queue_manager`` and ``inbox_router`` so the queue MCP tools
    can reach the substrate.

    The two attribute annotations are ``object`` rather than the concrete
    ``QueueManager`` / ``InboxRouter`` types to avoid an import cycle
    (``aegis.queue`` may later need bridge types); the runtime isinstance
    check is structural — the attributes just need to exist.
    """

    queue_manager: object        # QueueManager
    inbox_router: object         # InboxRouter
    canvas_manager: object       # CanvasManager
    terminal_manager: object     # TerminalManager
    groups: object               # GroupsBridge

    def list_sessions(self) -> list[SessionInfo]: ...
    def list_agents(self) -> list[str]: ...
    async def handoff(self, from_handle: str, target_handle: str,
                      context: str) -> str: ...
    async def spawn(self, profile: str, *,
                    handle: str | None = None) -> str: ...
    async def close(self, handle: str) -> None: ...
