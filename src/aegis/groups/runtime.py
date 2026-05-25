"""GroupRuntime — the façade the MCP layer + workflow engine call."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from aegis.groups.broadcast import BroadcastTracker
from aegis.groups.models import (
    BroadcastRecord,
    GroupResult,
    MemberResult,
)
from aegis.groups.persistence import (
    event_broadcast_completed,
    event_broadcast_started,
    event_member_result,
)
from aegis.groups.reducers import get_reducer
from aegis.groups.registry import GroupRegistry, UnknownGroup
from aegis.queue.inbox import InboxRouter
from aegis.queue.schema import InboxMessage, new_ulid, now_iso


def _sender_group_broadcast(group: str, broadcast_id: str) -> str:
    return f"group:{group}/broadcast:{broadcast_id}"


def _sender_group_cancel(group: str, broadcast_id: str) -> str:
    return f"group:{group}/cancel:{broadcast_id}"


def _compose_broadcast_body(objective: str, output_format: str,
                            tool_guidance: str, boundaries: str) -> str:
    return (
        f"objective: {objective}\n"
        f"output_format: {output_format}\n"
        f"tool_guidance: {tool_guidance}\n"
        f"boundaries: {boundaries}"
    )


@dataclass
class GroupRuntime:
    registry: GroupRegistry
    inbox: InboxRouter
    member_bus: asyncio.Queue
    now: Callable[[], str] = now_iso
    new_id: Callable[[], str] = new_ulid
    tracker: BroadcastTracker = None  # type: ignore[assignment]
    log: Any = None

    def __post_init__(self) -> None:
        if self.tracker is None:
            self.tracker = BroadcastTracker()

    def _emit(self, group: str, rec: dict[str, Any]) -> None:
        if self.log is not None:
            self.log.write(group, rec)

    async def broadcast(self, group: str, *, sender: str, objective: str,
                        output_format: str, tool_guidance: str,
                        boundaries: str) -> str:
        g = self.registry.get(group)
        rec = BroadcastRecord(
            id=self.new_id(), group=group, sender=sender,
            objective=objective, output_format=output_format,
            tool_guidance=tool_guidance, boundaries=boundaries,
            started_at=self.now(), members=tuple(sorted(g.members)),
        )
        self.tracker.open(rec)
        self._emit(group, event_broadcast_started(
            rec.id, objective, output_format, tool_guidance, boundaries,
            sender, rec.members))
        body = _compose_broadcast_body(objective, output_format,
                                       tool_guidance, boundaries)
        tag = _sender_group_broadcast(group, rec.id)
        for handle in rec.members:
            msg = InboxMessage(
                sender=tag, body=body, timestamp=self.now(),
            )
            await self.inbox.deliver(handle, msg)
        return rec.id

    async def wait_all(self, group: str, *, timeout: float = 600.0,
                       reducer: str = "concat") -> GroupResult:
        rec = self.tracker.current(group)
        if rec is None:
            raise UnknownGroup(f"no open broadcast on {group!r}")
        return await self._collect(
            rec, want={*rec.members}, timeout=timeout, reducer=reducer,
            wait_any=False,
        )

    async def wait_any(self, group: str, *, timeout: float = 600.0,
                       cancel_losers: bool = True) -> GroupResult:
        rec = self.tracker.current(group)
        if rec is None:
            raise UnknownGroup(f"no open broadcast on {group!r}")
        result = await self._collect(
            rec, want={*rec.members}, timeout=timeout, reducer="concat",
            wait_any=True,
        )
        if cancel_losers and result.by_member:
            winner = next(iter(result.by_member))
            tag = _sender_group_cancel(group, rec.id)
            body = f"superseded by {winner}"
            for handle in rec.members:
                if handle == winner:
                    continue
                await self.inbox.deliver(handle, InboxMessage(
                    sender=tag, body=body, timestamp=self.now(),
                ))
        return result

    async def _collect(self, rec: BroadcastRecord, *, want: set[str],
                       timeout: float, reducer: str,
                       wait_any: bool) -> GroupResult:
        by_member: dict[str, MemberResult] = {}
        order: list[str] = []
        deadline = asyncio.get_event_loop().time() + timeout
        while want and not (wait_any and by_member):
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            try:
                handle, text = await asyncio.wait_for(
                    self.member_bus.get(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            if handle not in want:
                continue
            want.discard(handle)
            order.append(handle)
            by_member[handle] = MemberResult(
                handle=handle, text=text, turn_ms=0,
                tokens_in=0, tokens_out=0, status="done",
            )
            self._emit(rec.group, event_member_result(
                rec.id, handle, "done", text[:200], 0, 0, 0))
        timeouts = sorted(want)
        combined: Any = get_reducer(reducer)(by_member, order)
        self.tracker.close(rec.group, rec.id)
        mode = "wait_any" if wait_any else "wait_all"
        self._emit(rec.group, event_broadcast_completed(
            rec.id, mode, reducer, self.now()))
        return GroupResult(
            broadcast_id=rec.id, by_member=by_member, combined=combined,
            errors={}, timeouts=timeouts,
        )
