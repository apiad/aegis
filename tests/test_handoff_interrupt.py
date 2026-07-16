"""aegis_handoff(interrupt=True) cuts the target's live turn before delivering."""
from __future__ import annotations

import pytest

from aegis.queue import Delivery


class _Info:
    def __init__(self, handle, state):
        self.handle = handle
        self.state = state
        self.agent_slug = "default"
        self.active = False
        self.unseen = False
        self.spawned_by = None


class _Inbox:
    def __init__(self):
        self.delivered = []

    async def deliver(self, handle, msg):
        self.delivered.append((handle, msg.body))
        return Delivery(disposition="landed", depth=0)


class _Bridge:
    def __init__(self, target_state):
        self.inbox_router = _Inbox()
        self.interrupted = []
        self._sessions = [_Info("alpha", "ready"),
                          _Info("beta", target_state)]

    def list_sessions(self):
        return list(self._sessions)

    async def interrupt(self, handle):
        self.interrupted.append(handle)


@pytest.mark.asyncio
async def test_handoff_interrupt_cuts_working_target():
    from aegis.mcp.server import make_handoff
    bridge = _Bridge(target_state="working")
    handoff = make_handoff(bridge)
    out = await handoff("alpha", "beta", "stop, wrong file", interrupt=True)
    assert bridge.interrupted == ["beta"]
    assert bridge.inbox_router.delivered == [("beta", "stop, wrong file")]
    assert out == "interrupted & landed at beta"


@pytest.mark.asyncio
async def test_handoff_interrupt_idle_target_is_plain_land():
    from aegis.mcp.server import make_handoff
    bridge = _Bridge(target_state="ready")
    handoff = make_handoff(bridge)
    out = await handoff("alpha", "beta", "fyi", interrupt=True)
    assert bridge.interrupted == []
    assert out == "landed at beta"


@pytest.mark.asyncio
async def test_handoff_default_does_not_interrupt():
    from aegis.mcp.server import make_handoff
    bridge = _Bridge(target_state="working")
    handoff = make_handoff(bridge)
    out = await handoff("alpha", "beta", "later")
    assert bridge.interrupted == []
    assert out == "queued for beta (position 0)"
