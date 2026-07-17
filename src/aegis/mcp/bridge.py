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
    spawned_by: str | None = None


class GroupsBridge(Protocol):
    """Concrete surface for the aegis_group_* MCP tools."""

    def list_groups(self) -> list[dict]: ...
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
    async def status(self, group: str) -> dict: ...
    async def dissolve(self, group: str) -> dict: ...
    async def rename(self, old: str, new: str) -> dict: ...
    async def move_member(self, handle: str, *, from_group: str,
                          to_group: str) -> dict: ...


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
    locks: object                # _LocksBridge
    remotes: object              # dict[str, RemoteSpec]; empty when none configured
    scheduler: object            # Scheduler | None
    state_root: object           # Path — workspace root
    workflow_registry: object    # has .get(name) -> WorkflowFn | None

    def inline_schedule_names(self) -> set[str]: ...

    def list_sessions(self) -> list[SessionInfo]: ...
    def list_agents(self) -> list[str]: ...
    async def handoff(self, from_handle: str, target_handle: str,
                      context: str) -> str: ...
    async def spawn(self, profile: str, *,
                    handle: str | None = None,
                    opening_prompt: str | None = None,
                    spawned_by: str | None = None) -> str: ...
    async def close(self, handle: str) -> None: ...
    async def interrupt(self, handle: str) -> None: ...
    async def rename_handle(self, old: str, new: str) -> dict: ...

    def register_agent(self, slug: str, agent: object) -> None:
        """Add a freshly-validated Agent to the live agent map. Idempotent
        on identical (slug, agent) pairs; raises ValueError on slug
        collision with a different agent."""
        ...

    def register_queue(self, queue: object) -> None:
        """Add a freshly-validated Queue to the live QueueManager.
        Raises ValueError on name collision."""
        ...

    def reload_plugins(self) -> None:
        """Re-run import_plugins(load_config(state_root)) so newly-added
        plugin_dirs entries register their @workflow functions."""
        ...
