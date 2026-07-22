"""ReminderService — self-left notes an agent delivers back to its own inbox.

Two timings, one surface (``aegis_remind``):

- **turn-end** (``after`` omitted): the note is buffered on the agent's live
  ``AgentSession`` (``add_reminder``) and fires as the session's LAST turn —
  strictly behind buffered inbox messages and any unsolicited harness-event
  drain (see ``AgentSession._chain_if_pending``). This service just routes the
  call to the session; the ordering discipline lives in the session core.

- **future-time** (``after`` given): a lightweight asyncio timer sleeps for the
  delay, then delivers the note through the ``InboxRouter`` as an ordinary
  inbox message (waking the agent if idle, buffering if busy).

In-memory only — pending future-time timers do not survive an ``aegis serve``
restart (matching monitors, and moot anyway since sessions are subprocesses
that die on restart). No JSONL persistence in v1.
"""
from __future__ import annotations

import asyncio
import contextlib
import re
import time
from collections.abc import Callable
from dataclasses import dataclass

from aegis.queue.schema import (
    InboxMessage,
    new_ulid,
    now_iso,
    sender_reminder,
)

_DURATION_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([smhd])", re.IGNORECASE)
_UNIT_SECONDS = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}


def parse_after(after: str | float | int) -> float:
    """Normalize an ``after`` argument to a float number of seconds.

    Accepts a raw number (already seconds) or a duration string like
    ``"90"``, ``"30s"``, ``"20m"``, ``"2h"``, ``"1h30m"``. Raises
    ``ValueError`` on an unparseable / non-positive value.
    """
    if isinstance(after, (int, float)):
        seconds = float(after)
    else:
        text = str(after).strip()
        if not text:
            raise ValueError("empty duration")
        # A bare number string is seconds.
        try:
            seconds = float(text)
        except ValueError:
            matches = _DURATION_RE.findall(text)
            if not matches:
                raise ValueError(f"unparseable duration: {after!r}") from None
            # Reject stray characters between unit tokens (e.g. "20x").
            if _DURATION_RE.sub("", text).strip():
                raise ValueError(f"unparseable duration: {after!r}")
            seconds = sum(float(n) * _UNIT_SECONDS[u.lower()]
                          for n, u in matches)
    if seconds <= 0:
        raise ValueError(f"duration must be positive: {after!r}")
    return seconds


@dataclass
class _Pending:
    id: str
    from_handle: str
    note: str
    fire_at: str          # iso8601
    delay_s: float
    task: asyncio.Task | None = None


class ReminderService:
    def __init__(self, inbox_router, session_manager=None, *,
                 clock: Callable[[], float] = time.time,
                 now: Callable[[], str] = now_iso) -> None:
        self._inbox = inbox_router
        self._sm = session_manager
        self._clock = clock
        self._now = now
        self._pending: dict[str, _Pending] = {}

    def _session_for(self, handle: str):
        get = getattr(self._sm, "get", None)
        return get(handle) if callable(get) else None

    # ----- entry point ----------------------------------------------
    def remind(self, *, from_handle: str, note: str,
               after: str | float | None = None) -> dict:
        if after is None:
            return self._remind_turn_end(from_handle, note)
        try:
            delay_s = parse_after(after)
        except ValueError as e:
            return {"error": str(e)}
        return self._remind_future(from_handle, note, delay_s)

    def _remind_turn_end(self, from_handle: str, note: str) -> dict:
        session = self._session_for(from_handle)
        if session is None:
            return {"error": f"no live session for handle {from_handle!r}"}
        msg = InboxMessage(
            sender=sender_reminder(), timestamp=self._now(), body=note)
        session.add_reminder(msg)
        return {"reminder_id": new_ulid(), "when": "turn_end"}

    def _remind_future(self, from_handle: str, note: str,
                       delay_s: float) -> dict:
        rid = new_ulid()
        fire_at = _iso_after(delay_s)
        pending = _Pending(id=rid, from_handle=from_handle, note=note,
                           fire_at=fire_at, delay_s=delay_s)
        self._pending[rid] = pending
        pending.task = asyncio.create_task(self._fire_after(rid))
        return {"reminder_id": rid, "when": fire_at}

    async def _fire_after(self, rid: str) -> None:
        pending = self._pending.get(rid)
        if pending is None:
            return
        try:
            await asyncio.sleep(pending.delay_s)
        except asyncio.CancelledError:
            raise
        # Re-check: a cancel between wake and delivery drops it.
        if self._pending.pop(rid, None) is None:
            return
        msg = InboxMessage(
            sender=sender_reminder(), timestamp=self._now(), body=pending.note)
        with contextlib.suppress(Exception):
            await self._inbox.deliver(pending.from_handle, msg)

    # ----- introspection / lifecycle --------------------------------
    def list_reminders(self, *, from_handle: str | None = None) -> list[dict]:
        return [
            {"reminder_id": p.id, "from_handle": p.from_handle,
             "note": p.note, "fire_at": p.fire_at}
            for p in self._pending.values()
            if from_handle is None or p.from_handle == from_handle
        ]

    def cancel(self, reminder_id: str) -> dict:
        pending = self._pending.pop(reminder_id, None)
        if pending is None:
            return {"ok": False,
                    "error": f"unknown reminder {reminder_id!r}"}
        if pending.task is not None:
            pending.task.cancel()
        return {"ok": True, "reminder_id": reminder_id}

    def reap(self, handle: str) -> None:
        """Cancel a dead session's pending future-time reminders."""
        for rid, p in list(self._pending.items()):
            if p.from_handle == handle:
                self._pending.pop(rid, None)
                if p.task is not None:
                    p.task.cancel()


def _iso_after(delay_s: float) -> str:
    from datetime import datetime, timedelta, timezone
    fire = datetime.now(timezone.utc) + timedelta(seconds=delay_s)
    return fire.strftime("%Y-%m-%dT%H:%M:%SZ")
