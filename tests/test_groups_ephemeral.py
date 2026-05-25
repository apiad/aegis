from __future__ import annotations

import asyncio

import pytest

from aegis.groups.registry import GroupRegistry, UnknownGroup
from aegis.groups.runtime import GroupRuntime
from aegis.groups.wiring import GroupWiring
from aegis.queue.inbox import InboxRouter
from aegis.workflow.engine import WorkflowEngine
from tests.fixtures.fake_groups_env import FakeManager


@pytest.mark.asyncio
async def test_ephemeral_group_dissolves_on_exit():
    bus: asyncio.Queue = asyncio.Queue()
    mgr = FakeManager()
    reg = GroupRegistry()
    router = InboxRouter()
    wiring = GroupWiring(session_manager=mgr, registry=reg, inbox=router,
                         member_bus=bus)
    rt = GroupRuntime(registry=reg, inbox=router, member_bus=bus,
                      new_id=lambda: "br-1", now=lambda: "T")

    engine = WorkflowEngine(name="t", workflow_id="w",
                            groups_runtime=rt, groups_wiring=wiring,
                            session_manager=mgr)

    captured: dict = {}
    async with engine.ephemeral_group(profiles=["p", "p"]) as g:
        captured["name"] = g.name
        captured["members_during"] = set(reg.get(g.name).members)

    with pytest.raises(UnknownGroup):
        reg.get(captured["name"])
    assert len(captured["members_during"]) == 2
