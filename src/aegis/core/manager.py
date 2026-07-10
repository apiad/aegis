from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from pathlib import Path

from aegis.core.session import AgentSession
from aegis.mcp.bridge import SessionInfo
from aegis.tui.names import generate_name
from aegis.tui.state import AgentState

SessionFactory = Callable[[object, str, str], object]

# 2 or 3 hyphen-separated alnum segments. First char must be a letter
# (so the handle doesn't read like a version string). Keeps handles
# greppable and URL-safe; rules out empties, uppercase, whitespace, and
# trailing/leading hyphens.
_HANDLE_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+){1,2}$")


def is_valid_handle(s: str) -> bool:
    return bool(_HANDLE_RE.match(s))


class SessionManager:
    """Frontend-agnostic owner of live AgentSessions. Is an AppBridge."""

    def __init__(self, agents: dict, default_agent: str,
                 make_session: SessionFactory, mcp,
                 *, inbox=None) -> None:
        self._agents = agents
        self._default_agent = default_agent
        self._make_session = make_session
        self._mcp = mcp
        self._inbox = inbox
        # AppBridge surface attrs. inbox_router is bound at construction;
        # queue_manager is attached after construction so cli._serve can
        # pass `self` to the QueueManager (avoids the chicken/egg).
        self.inbox_router = inbox
        self.queue_manager = None
        self.canvas_manager = None
        self.terminal_manager = None
        self.remotes: dict = {}  # populated by cli.serve from loaded YAML
        self.remote_plane = None  # populated by cli.serve from loaded YAML
        self.scheduler = None  # populated by cli.serve if schedules configured
        self.state_root: Path | None = None
        self._persist_dir = None
        self.workflow_registry = None
        self._inline_schedule_names: set[str] = set()
        self._sessions: list[AgentSession] = []
        self._mru: list[str] = []  # most-recently-active first
        from aegis.groups.bridge import make_groups_bridge
        self.groups = make_groups_bridge(
            session_manager=self, inbox_router=inbox)
        from aegis.locks.bridge import make_locks_bridge
        self.locks = make_locks_bridge(
            live_handles=self.live_handles,
            root_fn=lambda: self.state_root or Path.cwd(),
            state_dir=None)  # in-memory v1; live-handle filter reaps dead holders

    def attach_queue_manager(self, qm) -> None:
        self.queue_manager = qm

    def attach_locks_state(self, state_dir) -> None:
        """Turn on JSONL persistence for the claims registry (serve/web).
        Call once at boot before any claim exists; replays any prior log so
        claims survive a `serve` restart, matching the TUI which persists by
        default."""
        from aegis.locks.bridge import make_locks_bridge
        self.locks = make_locks_bridge(
            live_handles=self.live_handles,
            root_fn=lambda: self.state_root or Path.cwd(),
            state_dir=state_dir)

    def attach_remotes(self, remotes: dict) -> None:
        self.remotes = remotes

    def attach_remote_plane(self, remote_plane) -> None:
        self.remote_plane = remote_plane

    def attach_canvas_manager(self, cm) -> None:
        self.canvas_manager = cm

    def attach_terminal_manager(self, tm) -> None:
        self.terminal_manager = tm

    def attach_scheduler_context(self, *, scheduler, state_root,
                                 workflow_registry,
                                 inline_schedule_names: set[str]) -> None:
        self.scheduler = scheduler
        self.state_root = state_root
        self.workflow_registry = workflow_registry
        self._inline_schedule_names = set(inline_schedule_names)

    def inline_schedule_names(self) -> set[str]:
        return set(self._inline_schedule_names)

    def attach_persistence(self, state_dir) -> None:
        """Persist every spawned session's events to JSONL under state_dir.
        Called by the serve path; the in-process TUI does not call it (it
        persists via its own pane observer), so there is no double-write."""
        self._persist_dir = state_dir

    def register_agent(self, slug: str, agent) -> None:
        existing = self._agents.get(slug)
        if existing is not None:
            if existing == agent:
                return
            raise ValueError(f"agent {slug!r} already registered")
        self._agents[slug] = agent

    def register_queue(self, queue) -> None:
        if self.queue_manager is None:
            raise RuntimeError(
                "no queue_manager attached; cannot register queue")
        self.queue_manager.register_queue(queue)

    def reload_plugins(self) -> None:
        from pathlib import Path

        from aegis.config import yaml_loader
        root = self.state_root or Path.cwd()
        cfg = yaml_loader.load_config(root)
        yaml_loader.import_plugins(cfg)

    def _sync_spawn(self, slug: str | None = None, *,
                    opening_prompt: str | None = None,
                    handle: str | None = None,
                    spawned_by: str | None = None) -> AgentSession:
        slug = slug or self._default_agent
        if slug not in self._agents:
            raise KeyError(slug)
        agent = self._agents[slug]
        h = handle or generate_name({s.handle for s in self._sessions})
        url = self._mcp.url if self._mcp is not None else ""
        s = AgentSession(self._make_session(agent, url, h),
                         agent, slug, h,
                         inbox=self._inbox,
                         opening_prompt=opening_prompt)
        s.spawned_by = spawned_by
        if self._inbox is not None:
            self._inbox.bind_session(h, s)
        self._sessions.append(s)
        if self._persist_dir is not None:
            from aegis.state.session_log import make_session_log_observer
            s.add_event_observer(make_session_log_observer(self._persist_dir, h))
        self._touch(h)
        if opening_prompt is not None:
            asyncio.create_task(s.send(opening_prompt))
        return s

    async def spawn(self, profile: str, *,
                    handle: str | None = None,
                    opening_prompt: str | None = None,
                    spawned_by: str | None = None) -> str:
        """AppBridge-shaped async spawn. Returns the new handle."""
        sess = self._sync_spawn(profile, handle=handle,
                                opening_prompt=opening_prompt,
                                spawned_by=spawned_by)
        return sess.handle

    def _touch(self, handle: str) -> None:
        if handle in self._mru:
            self._mru.remove(handle)
        self._mru.insert(0, handle)

    def get(self, handle: str) -> AgentSession | None:
        return next((s for s in self._sessions if s.handle == handle), None)

    async def close(self, handle: str) -> None:
        s = self.get(handle)
        if s is None:
            return
        await s.close()
        self._sessions.remove(s)
        if self._inbox is not None:
            self._inbox.unbind_session(handle)
        if handle in self._mru:
            self._mru.remove(handle)

    async def interrupt(self, handle: str) -> None:
        s = self.get(handle)
        if s is not None:
            await s.interrupt()

    async def close_all(self) -> None:
        for s in list(self._sessions):
            await s.close()
            if self._inbox is not None:
                self._inbox.unbind_session(s.handle)
        self._sessions.clear()
        self._mru.clear()

    # --- AppBridge --------------------------------------------------------
    def list_sessions(self) -> list[SessionInfo]:
        top = self._mru[0] if self._mru else None
        return [
            SessionInfo(handle=s.handle, agent_slug=s.agent_slug,
                        state=s.state.value, active=(s.handle == top),
                        unseen=False,
                        spawned_by=getattr(s, "spawned_by", None))
            for s in self._sessions
        ]

    def list_agents(self) -> list[str]:
        return sorted(self._agents)

    def live_handles(self) -> set[str]:
        return {s.handle for s in self._sessions}

    async def rename_handle(self, old: str, new: str) -> dict:
        """Swap a live session's handle. Used by the ``aegis_rename`` MCP
        tool so an agent can give itself a more meaningful name once the
        session's purpose has settled.

        Returns ``{"ok": True, "old": old, "new": new}`` on success or
        ``{"error": "..."}`` on validation failure / unknown old / collision.
        ``old == new`` is a no-op success.
        """
        if old == new:
            session = self.get(old)
            if session is None:
                return {"error": f"no session {old!r}"}
            return {"ok": True, "old": old, "new": new}
        if not is_valid_handle(new):
            return {"error":
                    f"new handle {new!r} fails format: must be 2-3 "
                    f"kebab-case alphanumeric segments, starting with a "
                    f"letter (e.g. 'lucid-river-runs')"}
        session = self.get(old)
        if session is None:
            return {"error":
                    f"no session {old!r} (use aegis_list_sessions)"}
        if self.get(new) is not None:
            return {"error":
                    f"handle {new!r} already in use by another session"}
        session.handle = new
        if old in self._mru:
            idx = self._mru.index(old)
            self._mru[idx] = new
        if self._inbox is not None:
            self._inbox.rename(old, new)
        return {"ok": True, "old": old, "new": new}

    async def handoff(self, from_handle: str, target_handle: str,
                      context: str) -> str:
        if from_handle == target_handle:
            return "handoff rejected: cannot hand off to yourself"
        target = self.get(target_handle)
        if target is None:
            return (f"handoff rejected: no session {target_handle!r} "
                    f"(use aegis_list_sessions)")
        if target.state is AgentState.working:
            return (f"handoff rejected: {target_handle!r} is busy, "
                    f"retry shortly")
        await target.send(f"[handoff from {from_handle}] {context}")
        return f"delivered to {target_handle}"
