from __future__ import annotations

import asyncio

import pytest

from aegis.events import AssistantText, Result
from aegis.groups.registry import GroupRegistry
from aegis.groups.wiring import GroupWiring
from aegis.queue.inbox import InboxRouter


class _FakeSession:
    def __init__(self, handle: str):
        self.handle = handle
        self.delivered = []
        self._observers = []

    async def deliver(self, msg) -> None:
        self.delivered.append(msg)

    def add_event_observer(self, cb) -> None:
        self._observers.append(cb)

    def emit(self, ev) -> None:
        for cb in self._observers:
            cb(self, ev)


class _FakeManager:
    def __init__(self):
        self.sessions: dict[str, _FakeSession] = {}

    async def spawn(self, profile: str, *, handle: str | None = None,
                    **_):
        h = handle or f"{profile}-handle"
        s = _FakeSession(h)
        self.sessions[h] = s
        return h

    def get(self, handle: str):
        return self.sessions.get(handle)


@pytest.mark.asyncio
async def test_wiring_spawns_into_group_and_routes_turn_end():
    mgr = _FakeManager()
    reg = GroupRegistry()
    router = InboxRouter()
    bus: asyncio.Queue = asyncio.Queue()
    wiring = GroupWiring(session_manager=mgr, registry=reg, inbox=router,
                         member_bus=bus)

    handle = await wiring.spawn(profile="opus", group="rev", handle="ada")
    assert handle == "ada"
    assert "ada" in reg.get("rev").members
    assert router._sessions["ada"] is mgr.sessions["ada"]

    # Simulate a post-broadcast turn-end:
    mgr.sessions["ada"].emit(AssistantText(text="HEARD"))
    mgr.sessions["ada"].emit(Result(duration_ms=10, is_error=False))
    h, t = await asyncio.wait_for(bus.get(), 1)
    assert h == "ada" and t == "HEARD"
