from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from pathlib import Path

from aegis.config import Agent
from aegis.core.session import AgentSession
from aegis.mcp.bridge import SessionInfo
from aegis.queue import LoopService
from aegis.tui.names import generate_name
from aegis.tui.state import AgentState

SessionFactory = Callable[[object, str, str], object]


def _overlay_agent(base: Agent, *, model: str | None,
                   effort: str | None, prompt: str | None) -> Agent:
    """Return a copy of ``base`` with per-session overrides applied. The
    driver (``harness``) is preserved; only model/effort/prompt change. Used
    by interactive picks and ``aegis_spawn`` overrides — never persisted."""
    if model is None and effort is None and prompt is None:
        return base
    data = base.model_dump()
    if model is not None:
        data["model"] = model
        if data.get("provider"):
            data["provider"]["model"] = model
    if effort is not None:
        data["effort"] = effort
        if (data.get("provider") or {}).get("name") == "claude-code":
            data["provider"]["effort"] = effort
    if prompt is not None:
        data["prompt"] = prompt
    return Agent(**data)

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
        self.monitor_manager = None
        self.reminder_service = None
        self.loop_service = LoopService(self)
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

    def attach_monitor_manager(self, mm) -> None:
        self.monitor_manager = mm

    def attach_reminder_service(self, rs) -> None:
        self.reminder_service = rs

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
                    spawned_by: str | None = None,
                    model: str | None = None,
                    effort: str | None = None,
                    prompt: str | None = None,
                    fork_from: str | None = None,
                    forked_from: dict | None = None) -> AgentSession:
        slug = slug or self._default_agent
        if slug not in self._agents:
            raise KeyError(slug)
        agent = _overlay_agent(self._agents[slug], model=model,
                              effort=effort, prompt=prompt)
        h = handle or generate_name({s.handle for s in self._sessions})
        url = self._mcp.url if self._mcp is not None else ""
        # Only pass fork_from when forking: the factory signature predates
        # it and plain (profile, url, handle) callables must keep working.
        raw = (self._make_session(agent, url, h, fork_from=fork_from)
               if fork_from is not None
               else self._make_session(agent, url, h))
        s = AgentSession(raw, agent, slug, h,
                         inbox=self._inbox,
                         opening_prompt=opening_prompt)
        s.spawned_by = spawned_by
        s.forked_from = forked_from
        if self._inbox is not None:
            self._inbox.bind_session(h, s)
        self._sessions.append(s)
        if self._persist_dir is not None:
            from aegis.state.session_log import make_session_log_observer
            s.add_event_observer(
                make_session_log_observer(self._persist_dir, s.log_id))
        self._touch(h)
        if opening_prompt is not None:
            asyncio.create_task(s.send(opening_prompt))
        return s

    async def spawn(self, profile: str, *,
                    handle: str | None = None,
                    opening_prompt: str | None = None,
                    spawned_by: str | None = None,
                    model: str | None = None,
                    effort: str | None = None,
                    prompt: str | None = None) -> str:
        """AppBridge-shaped async spawn. Returns the new handle.

        ``model`` / ``effort`` / ``prompt`` are optional per-session
        overrides layered over the named profile (never persisted)."""
        sess = self._sync_spawn(profile, handle=handle,
                                opening_prompt=opening_prompt,
                                spawned_by=spawned_by,
                                model=model, effort=effort, prompt=prompt)
        return sess.handle

    def _fork_capability(self, harness: str) -> bool:
        """Whether the driver behind ``harness`` can branch a session."""
        from aegis.drivers import get_driver
        try:
            return bool(get_driver(harness).supports_fork)
        except Exception:  # noqa: BLE001 — unknown harness is a refusal
            return False

    async def fork(self, target: str, *,
                   prompt: str | None = None,
                   slug: str | None = None,
                   model: str | None = None,
                   effort: str | None = None,
                   forked_by: str | None = None) -> str:
        """Branch ``target``'s conversation into a new session.

        The parent is left entirely alone — same session id, same log,
        same tab. The child gets a new handle, a new log id, and an empty
        inbox; what it inherits is the harness-side conversation.

        ``prompt`` is the divergence and is optional: without one the fork
        inherits the conversation and waits for input.

        Raises ValueError listing every refusal reason at once.
        """
        from aegis.core.fork_guard import facts_for, refuse_reasons
        s = self.get(target)
        facts = facts_for(s, capability=self._fork_capability)
        reasons = refuse_reasons(facts, target=target)
        if reasons:
            raise ValueError("; ".join(reasons))
        # Snapshot the parent's session id NOW. A no-prompt fork typed
        # into ten minutes later must branch from where it was forked,
        # not from wherever the parent has drifted to since.
        sid = s.session_id
        child = self._sync_spawn(
            s.agent_slug, handle=slug, opening_prompt=prompt,
            model=model, effort=effort,
            fork_from=sid,
            forked_from={"handle": target, "log_id": s.log_id,
                         "session_id": sid})
        child.spawned_by = forked_by
        return child.handle

    async def side_note(self, handle: str, prompt: str):
        """AppBridge-shaped: a side note off this session's transcript."""
        from aegis.btw import SideNote, side_note_for
        s = self.get(handle)
        if s is None:
            return SideNote(error=f"unknown session: {handle}")
        state_dir = self._persist_dir or self.state_root
        if state_dir is None:
            return SideNote(
                error="this session has no persisted transcript to read")
        return await side_note_for(
            prompt, state_dir=state_dir, log_id=s.log_id, agent=s.agent,
            agents=self._agents, cwd=str(s.project_root))

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

    async def interrupt(self, handle: str, *, drain: bool = True) -> None:
        s = self.get(handle)
        if s is not None:
            await s.interrupt(drain=drain)

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
                        spawned_by=getattr(s, "spawned_by", None),
                        unsolicited=getattr(s, "unsolicited_turn", False))
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
        self.locks.rename(old, new)
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
