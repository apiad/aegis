"""RemoteSessionManager — conversation-loop AppBridge backed by WsClient.

Implements the subset of ``aegis.mcp.bridge.AppBridge`` that the TUI
conversation loop consumes, routing all operations to the remote aegis
server via ``WsClient`` RPC and stream subscriptions.

Auxiliary planes (queues, canvas, terminals, groups, workflow, scheduler)
are not yet exposed over the WS protocol; accessing them raises
``RemoteUnsupportedError``.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from aegis.mcp.bridge import SessionInfo
from aegis.state.event_codec import decode_event
from aegis.tui.ws_client import WsClient


class RemoteUnsupportedError(RuntimeError):
    """Raised when a --remote v1 TUI touches an auxiliary plane
    (queues, canvas, terminals, groups, workflow, scheduler) that isn't
    yet exposed over the WS protocol."""


_MSG = "not available in --remote v1"


class _DisabledPlane:
    """Sentinel that raises RemoteUnsupportedError on any attribute or
    method access, with a stable message the TUI catches to show its
    'not available in --remote v1' banner."""

    def __init__(self, name: str) -> None:
        self._name = name

    def __getattr__(self, item: str):
        raise RemoteUnsupportedError(f"{self._name}.{item}: {_MSG}")


@dataclass
class _Delivery:
    disposition: str
    depth: int


class RemoteAgentSession:
    """Thin proxy for a single remote session, mirroring the AgentSession
    interface the conversation loop uses (add_*_observer, deliver)."""

    def __init__(self, handle: str, ws: WsClient) -> None:
        self.handle = handle
        self._ws = ws
        self._event_obs: list[Callable] = []
        self._state_obs: list[Callable] = []
        self._inbox_obs: list[Callable] = []

    def add_event_observer(self, cb: Callable) -> None:
        self._event_obs.append(cb)

    def add_state_observer(self, cb: Callable) -> None:
        self._state_obs.append(cb)

    def add_inbox_observer(self, cb: Callable) -> None:
        self._inbox_obs.append(cb)

    async def deliver(self, msg) -> _Delivery:
        r = await self._ws.rpc("deliver", {"handle": self.handle,
                                            "message": msg.body})
        return _Delivery(disposition=r["delivery"], depth=r["depth"])


class RemoteSessionManager:
    """AppBridge implementation (conversation-loop subset) backed by WsClient.

    Call ``await mgr.start()`` after constructing to subscribe to the
    session_list stream and populate the initial sessions map.
    """

    def __init__(self, ws: WsClient, *, cwd: Path | None = None) -> None:
        self._ws = ws
        self._sessions: dict[str, RemoteAgentSession] = {}
        self._infos: dict[str, SessionInfo] = {}
        self._agents: list[str] = []

        # AppBridge auxiliary plane stubs — all disabled in v1
        self.queue_manager = _DisabledPlane("queue_manager")
        self.inbox_router = _DisabledPlane("inbox_router")
        self.canvas_manager = _DisabledPlane("canvas_manager")
        self.terminal_manager = _DisabledPlane("terminal_manager")
        self.groups = _DisabledPlane("groups")
        self.locks = _DisabledPlane("locks")
        self.workflow_registry = _DisabledPlane("workflow_registry")
        self.remotes: dict = {}
        self.scheduler = None
        self.state_root: Path = cwd or Path.cwd()

    async def start(self) -> None:
        """Subscribe to session_list; pre-populate _sessions map from the
        initial ``list_sessions`` RPC result."""
        self._ws.on("event", self._on_event)
        self._ws.on("state", self._on_state)
        self._ws.on("inbox", self._on_inbox)
        self._ws.on("session_list", self._on_session_list)
        r = await self._ws.rpc("list_sessions", {})
        for si in r.get("sessions", []):
            self._add_session(si)
        await self._ws.subscribe_global("session_list")

    # ------------------------------------------------------------------
    # AppBridge conversation-loop methods
    # ------------------------------------------------------------------

    async def spawn(self, profile: str, *,
                    handle: str | None = None,
                    opening_prompt: str | None = None,
                    spawned_by: str | None = None) -> str:
        params: dict = {"agent_profile": profile}
        if handle is not None:
            params["handle"] = handle
        if opening_prompt is not None:
            params["opening_prompt"] = opening_prompt
        if spawned_by is not None:
            params["spawned_by"] = spawned_by
        r = await self._ws.rpc("spawn_session", params)
        return r["handle"]

    async def close(self, handle: str) -> None:
        await self._ws.rpc("close_session", {"handle": handle})
        self._sessions.pop(handle, None)
        self._infos.pop(handle, None)

    async def interrupt(self, handle: str) -> None:
        await self._ws.rpc("interrupt_session", {"handle": handle})

    async def handoff(self, from_handle: str, target_handle: str,
                      context: str) -> str:
        r = await self._ws.rpc("handoff", {
            "from_handle": from_handle,
            "target_handle": target_handle,
            "context": context,
        })
        return r["result"]

    async def rename_handle(self, old: str, new: str) -> dict:
        return await self._ws.rpc("rename_handle", {"old": old, "new": new})

    def list_sessions(self) -> list[SessionInfo]:
        return list(self._infos.values())

    def list_agents(self) -> list[str]:
        return list(self._agents)

    def get(self, handle: str) -> RemoteAgentSession | None:
        return self._sessions.get(handle)

    def inline_schedule_names(self) -> set[str]:
        return set()

    def register_agent(self, slug: str, agent: object) -> None:
        raise RemoteUnsupportedError(f"register_agent: {_MSG}")

    def register_queue(self, queue: object) -> None:
        raise RemoteUnsupportedError(f"register_queue: {_MSG}")

    def reload_plugins(self) -> None:
        raise RemoteUnsupportedError(f"reload_plugins: {_MSG}")

    # ------------------------------------------------------------------
    # Internal stream dispatch
    # ------------------------------------------------------------------

    def _add_session(self, si: dict) -> None:
        info = SessionInfo(
            handle=si["handle"],
            agent_slug=si["agent_slug"],
            state=si["state"],
            active=si["active"],
            unseen=si["unseen"],
            spawned_by=si.get("spawned_by"),
        )
        self._infos[info.handle] = info
        self._sessions.setdefault(info.handle,
                                   RemoteAgentSession(info.handle, self._ws))

    def _on_event(self, fr: dict) -> None:
        sess = self._sessions.get(fr.get("handle", ""))
        if sess is None:
            return
        try:
            ev = decode_event(fr["event"])
        except Exception:
            return
        for cb in list(sess._event_obs):
            try:
                cb(ev)
            except Exception:
                pass

    def _on_state(self, fr: dict) -> None:
        sess = self._sessions.get(fr.get("handle", ""))
        if sess is None:
            return
        for cb in list(sess._state_obs):
            try:
                cb(fr.get("state"), fr.get("metrics"))
            except Exception:
                pass

    def _on_inbox(self, fr: dict) -> None:
        sess = self._sessions.get(fr.get("handle", ""))
        if sess is None:
            return
        for cb in list(sess._inbox_obs):
            try:
                cb(fr.get("msg"))
            except Exception:
                pass

    def _on_session_list(self, fr: dict) -> None:
        for si in fr.get("added", []) or []:
            self._add_session(si)
        for h in fr.get("removed", []) or []:
            self._sessions.pop(h, None)
            self._infos.pop(h, None)
        for si in fr.get("updated", []) or []:
            self._add_session(si)  # upsert
