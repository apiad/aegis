"""InboxRouter — per-handle delivery channel.

Pokes a live ``AgentSession`` when bound, otherwise buffers in-memory pending.
JSONL writethrough (the state-dir parameter) lands in VS2; this VS1 build is
memory-only.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from aegis.queue.schema import Delivery, InboxMessage

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

    def rename(self, old: str, new: str) -> None:
        """Move both the live-session binding and any pending messages
        from ``old`` to ``new``. No-op when ``old == new``.
        """
        if old == new:
            return
        session = self._sessions.pop(old, None)
        if session is not None:
            self._sessions[new] = session
        pending = self._pending.pop(old, None)
        if pending is not None:
            self._pending.setdefault(new, []).extend(pending)

    async def deliver(self, handle: str, msg: InboxMessage) -> Delivery:
        # Persist before any live-session signalling: the JSONL record is
        # the audit-log; an in-flight crash between writethrough and poke
        # still leaves the message recoverable from disk.
        if self._state_dir is not None:
            from dataclasses import asdict

            from aegis.queue.jsonl import append_record
            path = Path(self._state_dir) / "inboxes" / f"{handle}.jsonl"
            append_record(path, asdict(msg))
        session = self._sessions.get(handle)
        if session is not None:
            return await session.deliver(msg)
        # No live session: buffer in-memory; the message is queued until a
        # session binds and drains it.
        pending = self._pending.setdefault(handle, [])
        pending.append(msg)
        return Delivery(disposition="queued", depth=len(pending))

    def drain(self, handle: str) -> list[InboxMessage]:
        return self._pending.pop(handle, [])

    def pending(self, handle: str) -> list[InboxMessage]:
        return list(self._pending.get(handle, []))
