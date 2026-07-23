"""LoopService — handle→session routing for `/loop`.

The loop itself lives on ``AgentSession`` (``aegis/core/loop.py`` and the
tier in ``_chain_if_pending``). This service is the thin surface the MCP
plane and the slash-command plane share, mirroring ``ReminderService``: it
resolves a handle to a live session and validates arguments, nothing more.

No timers and no persistence — a loop dies with its session.
"""
from __future__ import annotations

from aegis.core.loop import DEFAULT_MAX_ITERATIONS


class LoopService:
    def __init__(self, session_manager=None) -> None:
        self._sm = session_manager

    def _session_for(self, handle: str):
        get = getattr(self._sm, "get", None)
        return get(handle) if callable(get) else None

    def arm(self, *, from_handle: str, text: str,
            max_iterations: int = DEFAULT_MAX_ITERATIONS) -> dict:
        if not text or not text.strip():
            return {"error": "loop text is empty"}
        try:
            max_iterations = int(max_iterations)
        except (TypeError, ValueError):
            return {"error": f"max must be an integer: {max_iterations!r}"}
        if max_iterations < 1:
            return {"error": f"max must be >= 1: {max_iterations}"}
        session = self._session_for(from_handle)
        if session is None:
            return {"error": f"no live session for handle {from_handle!r}"}
        session.arm_loop(text.strip(), max_iterations)
        return {"armed": True, "text": text.strip(),
                "max_iterations": max_iterations}

    def stop(self, *, from_handle: str, reason: str = "stopped") -> dict:
        session = self._session_for(from_handle)
        if session is None:
            return {"error": f"no live session for handle {from_handle!r}"}
        return {"stopped": session.stop_loop(reason), "reason": reason}

    def status(self, *, from_handle: str) -> dict:
        session = self._session_for(from_handle)
        if session is None:
            return {"error": f"no live session for handle {from_handle!r}"}
        return {"loop": session.loop_status()}
