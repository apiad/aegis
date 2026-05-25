from __future__ import annotations

import asyncio

import pytest

from aegis.groups.models import MemberRef
from aegis.groups.registry import GroupRegistry
from aegis.groups.runtime import GroupRuntime
from aegis.queue.inbox import InboxRouter


class _FS:
    def __init__(self, handle: str, bus: asyncio.Queue):
        self.handle = handle
        self.delivered = []
        self._bus = bus

    async def deliver(self, msg):
        self.delivered.append(msg)

    async def finish_turn(self, text):
        await self._bus.put((self.handle, text))


@pytest.mark.asyncio
async def test_wait_any_returns_first_finisher_and_marks_others_canceled():
    reg = GroupRegistry()
    reg.add_member("g", MemberRef("a", "p"))
    reg.add_member("g", MemberRef("b", "p"))
    reg.add_member("g", MemberRef("c", "p"))
    bus: asyncio.Queue = asyncio.Queue()
    router = InboxRouter()
    sessions = {h: _FS(h, bus) for h in ("a", "b", "c")}
    for h, s in sessions.items():
        router.bind_session(h, s)
    rt = GroupRuntime(registry=reg, inbox=router, member_bus=bus,
                      now=lambda: "T", new_id=lambda: "br-1")

    await rt.broadcast("g", sender="agent:host", objective="o",
                       output_format="f", tool_guidance="t", boundaries="b")

    async def drive():
        await sessions["b"].finish_turn("first")
    asyncio.create_task(drive())

    result = await rt.wait_any("g", timeout=2, cancel_losers=True)

    assert set(result.by_member) == {"b"}
    assert result.by_member["b"].text == "first"
    for loser in ("a", "c"):
        kinds = [m.sender for m in sessions[loser].delivered]
        assert any("group:g/cancel:br-1" in s for s in kinds)
