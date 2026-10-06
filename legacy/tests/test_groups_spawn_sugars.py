from __future__ import annotations

import asyncio

import pytest

from aegis.groups.registry import GroupRegistry
from aegis.groups.wiring import GroupWiring
from aegis.queue.inbox import InboxRouter
from tests.fixtures.fake_groups_env import FakeManager


@pytest.mark.asyncio
async def test_spawn_many_creates_n_members_with_same_profile():
    bus: asyncio.Queue = asyncio.Queue()
    mgr = FakeManager()
    reg = GroupRegistry()
    w = GroupWiring(session_manager=mgr, registry=reg,
                    inbox=InboxRouter(), member_bus=bus)
    handles = await w.spawn_many(profile="opus", n=3, group="rev")
    assert len(handles) == 3
    assert len(reg.get("rev").members) == 3


@pytest.mark.asyncio
async def test_spawn_group_creates_heterogeneous_members():
    bus: asyncio.Queue = asyncio.Queue()
    mgr = FakeManager()
    reg = GroupRegistry()
    w = GroupWiring(session_manager=mgr, registry=reg,
                    inbox=InboxRouter(), member_bus=bus)
    handles = await w.spawn_group("rev", ["sec", "style", "logic"])
    assert len(handles) == 3
    profiles = {m.profile for m in reg.get("rev").members.values()}
    assert profiles == {"sec", "style", "logic"}


@pytest.mark.asyncio
async def test_spawn_many_rejects_n_zero():
    bus: asyncio.Queue = asyncio.Queue()
    w = GroupWiring(session_manager=FakeManager(), registry=GroupRegistry(),
                    inbox=InboxRouter(), member_bus=bus)
    with pytest.raises(ValueError):
        await w.spawn_many(profile="opus", n=0, group="rev")
