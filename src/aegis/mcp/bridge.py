from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from aegis.plan import PlanSnapshot


@dataclass(frozen=True)
class SessionInfo:
    handle: str
    agent_slug: str
    state: str          # AgentState.value: "ready" | "working" | "error"
    active: bool
    unseen: bool
    spawned_by: str | None = None
    # True when the current ``working`` turn is an unsolicited drain (the
    # harness emitting post-Result events on its own, e.g. a Claude
    # background-task notification) rather than a real agent turn. Consumers
    # like MonitorManager use this to avoid interrupting a self-resolving turn.
    unsolicited: bool = False
    # Which machine this session's harness runs on: "local", or a key from
    # the `hosts:` config. Paths in a remote session's transcript name
    # files on THAT machine, so consumers must not treat them as local.
    host: str = "local"
    # Plan roll-up, so a peer deciding who to hand work to also learns how
    # far along everyone is and what they are on. None means the session
    # has no plan — distinct from a plan with no tasks, which is 0/0.
    plan: "PlanSnapshot | None" = None


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
    monitor_manager: object      # MonitorManager
    reminder_service: object     # ReminderService
    loop_service: object         # LoopService
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
    def plan_state(self, handle: str): ...
    def list_agents(self) -> list[str]: ...
    async def handoff(self, from_handle: str, target_handle: str,
                      context: str) -> str: ...
    async def spawn(self, profile: str, *,
                    handle: str | None = None,
                    opening_prompt: str | None = None,
                    spawned_by: str | None = None,
                    model: str | None = None,
                    effort: str | None = None,
                    prompt: str | None = None,
                    host: str | None = None,
                    cwd: str | None = None) -> str: ...
    async def fork(self, target: str, *,
                   prompt: str | None = None,
                   slug: str | None = None,
                   model: str | None = None,
                   effort: str | None = None,
                   forked_by: str | None = None) -> str:
        """Branch ``target``'s conversation into a new session.

        Raises ValueError listing every refusal reason at once — no
        session id yet, driver cannot fork, or the target is mid-turn.
        """
        ...
    async def side_note(self, handle: str, prompt: str):
        """Answer a side question from ``handle``'s own transcript tail.

        Never touches the harness session — it reads aegis's own log and
        makes one independent one-shot call — so unlike ``fork`` it is
        legal while the pane is mid-turn.

        Best-effort by contract: returns a ``SideNote`` whose ``ok`` is
        False on any failure rather than raising, because a side question
        must not be able to disturb the conversation it sits beside.
        """
        ...

    async def peer_ask(self, from_handle: str, target: str, prompt: str,
                       *, cc: bool = False):
        """Ask an **idle** peer a question, from where the operator stands.

        The guard reads the target and never the source: asking an idle
        peer while your own tab is mid-turn is the point of the gesture,
        not an edge case. A busy target is refused rather than
        interrupted — it is already producing the value the ask was after.

        Best-effort by contract: returns a ``PeerAnswer`` whose ``ok`` is
        False on any failure rather than raising.
        """
        ...

    async def close(self, handle: str) -> None: ...
    async def interrupt(self, handle: str, *, drain: bool = True) -> None:
        """Cut the handle's live turn. ``drain=True`` (the default) then
        dispatches anything buffered in its inbox as the next turn; callers
        that deliver their own message immediately after pass ``drain=False``
        so both go out together."""
        ...
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
