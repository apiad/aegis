from __future__ import annotations

from collections.abc import Callable

from aegis.core.session import AgentSession
from aegis.mcp.bridge import SessionInfo
from aegis.tui.names import generate_name
from aegis.tui.state import AgentState

SessionFactory = Callable[[object, str, str], object]


class SessionManager:
    """Frontend-agnostic owner of live AgentSessions. Is an AppBridge."""

    def __init__(self, agents: dict, default_agent: str,
                 make_session: SessionFactory, mcp) -> None:
        self._agents = agents
        self._default_agent = default_agent
        self._make_session = make_session
        self._mcp = mcp
        self._sessions: list[AgentSession] = []
        self._mru: list[str] = []  # most-recently-active first

    def spawn(self, slug: str | None = None) -> AgentSession:
        slug = slug or self._default_agent
        if slug not in self._agents:
            raise KeyError(slug)
        agent = self._agents[slug]
        handle = generate_name({s.handle for s in self._sessions})
        url = self._mcp.url if self._mcp is not None else ""
        s = AgentSession(self._make_session(agent, url, handle),
                         agent, slug, handle)
        self._sessions.append(s)
        self._touch(handle)
        return s

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
        if handle in self._mru:
            self._mru.remove(handle)

    async def interrupt(self, handle: str) -> None:
        s = self.get(handle)
        if s is not None:
            await s.interrupt()

    async def close_all(self) -> None:
        for s in list(self._sessions):
            await s.close()
        self._sessions.clear()
        self._mru.clear()

    # --- AppBridge --------------------------------------------------------
    def list_sessions(self) -> list[SessionInfo]:
        top = self._mru[0] if self._mru else None
        return [
            SessionInfo(handle=s.handle, agent_slug=s.agent_slug,
                        state=s.state.value, active=(s.handle == top),
                        unseen=False)
            for s in self._sessions
        ]

    def list_agents(self) -> list[str]:
        return sorted(self._agents)

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
