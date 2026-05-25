from __future__ import annotations

import asyncio

import pytest

from aegis.groups.models import MemberRef
from aegis.groups.registry import GroupRegistry
from aegis.groups.runtime import GroupRuntime
from aegis.queue.inbox import InboxRouter


class FakeSession:
    def __init__(self, handle: str, bus: asyncio.Queue):
        self.handle = handle
        self.delivered: list = []
        self._bus = bus

    async def deliver(self, msg) -> None:
        self.delivered.append(msg)

    async def finish_turn(self, text: str) -> None:
        await self._bus.put((self.handle, text))


@pytest.mark.asyncio
async def test_wait_all_collects_one_turn_per_member_and_reduces():
    reg = GroupRegistry()
    reg.add_member("rev", MemberRef("ada",   "sec"))
    reg.add_member("rev", MemberRef("lucid", "logic"))

    bus: asyncio.Queue = asyncio.Queue()
    router = InboxRouter()
    sessions = {h: FakeSession(h, bus) for h in ("ada", "lucid")}
    for h, s in sessions.items():
        router.bind_session(h, s)

    rt = GroupRuntime(registry=reg, inbox=router, member_bus=bus,
                      now=lambda: "2026-05-25T08:00:00Z",
                      new_id=lambda: "br-1")

    bid = await rt.broadcast(
        "rev", sender="agent:host",
        objective="Reply HEARD.", output_format="one word",
        tool_guidance="none", boundaries="one turn",
    )
    assert bid == "br-1"
    assert len(sessions["ada"].delivered) == 1
    assert len(sessions["lucid"].delivered) == 1

    async def drive():
        await asyncio.sleep(0)
        await sessions["ada"].finish_turn("HEARD")
        await sessions["lucid"].finish_turn("HEARD")

    driver = asyncio.create_task(drive())
    result = await rt.wait_all("rev", timeout=5)
    await driver

    assert result.broadcast_id == "br-1"
    assert set(result.by_member) == {"ada", "lucid"}
    assert result.combined.startswith("---\nada:") or \
           result.combined.startswith("---\nlucid:")
    assert result.errors == {}
    assert result.timeouts == []


@pytest.mark.asyncio
async def test_wait_all_returns_timeouts_for_silent_members():
    reg = GroupRegistry()
    reg.add_member("rev", MemberRef("a", "p"))
    reg.add_member("rev", MemberRef("b", "p"))
    bus: asyncio.Queue = asyncio.Queue()
    router = InboxRouter()
    sessions = {h: FakeSession(h, bus) for h in ("a", "b")}
    for h, s in sessions.items():
        router.bind_session(h, s)
    rt = GroupRuntime(registry=reg, inbox=router, member_bus=bus,
                      now=lambda: "T", new_id=lambda: "br-1")
    await rt.broadcast("rev", sender="agent:host",
                       objective="x", output_format="y",
                       tool_guidance="z", boundaries="w")

    async def drive():
        await asyncio.sleep(0)
        await sessions["a"].finish_turn("done")

    driver = asyncio.create_task(drive())
    result = await rt.wait_all("rev", timeout=0.2)
    await driver
    assert "b" in result.timeouts
    assert "a" in result.by_member
