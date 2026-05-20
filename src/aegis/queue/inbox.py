"""InboxRouter — per-handle delivery channel.

Pokes a live ``AgentSession`` when bound, otherwise buffers in-memory pending.
JSONL writethrough (the state-dir parameter) lands in VS2; this VS1 build is
memory-only.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from aegis.queue.schema import InboxMessage

if TYPE_CHECKING:
    from aegis.core.session import AgentSession


class InboxRouter:
    def __init__(self, state_dir: Path | None = None) -> None:
        self._state_dir = state_dir
        self._pending: dict[str, list[InboxMessage]] = {}
        self._sessions: dict[str, "AgentSession"] = {}

    def bind_session(self, handle: str, session: "AgentSession") -> None:
        self._sessions[handle] = session

    def unbind_session(self, handle: str) -> None:
        self._sessions.pop(handle, None)

    async def deliver(self, handle: str, msg: InboxMessage) -> None:
        session = self._sessions.get(handle)
        if session is not None:
            await session.deliver(msg)
            return
        self._pending.setdefault(handle, []).append(msg)

    def drain(self, handle: str) -> list[InboxMessage]:
        return self._pending.pop(handle, [])

    def pending(self, handle: str) -> list[InboxMessage]:
        return list(self._pending.get(handle, []))
