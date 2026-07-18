from __future__ import annotations

import pytest

from aegis.dsl.interpreter import dynamic
from aegis.workflow.engine import WorkflowEngine


def _engine(bridge):
    return WorkflowEngine(
        bridge=bridge, workflow_id="wf1", name="dynamic", host="h")


async def test_sequence_spawns_and_sends_in_order(fake_bridge):
    fake_bridge.set_reply_sequence("worker-1", ["reply-a"])
    fake_bridge.set_reply_sequence("worker-2", ["reply-b"])
    spec = {
        "meta": {"name": "s1"},
        "root": {"type": "sequence", "children": [
            {"type": "agent", "id": "a", "prompt": "do a",
             "target": {"kind": "spawn", "profile": "worker"}},
            {"type": "agent", "id": "b", "prompt": "do b",
             "target": {"kind": "spawn", "profile": "worker"}},
        ]},
    }
    out = await dynamic(_engine(fake_bridge), spec=spec)
    assert fake_bridge.spawned_profiles == ["worker", "worker"]
    assert fake_bridge.sends_to("worker-1") == ["do a"]
    assert fake_bridge.sends_to("worker-2") == ["do b"]
    assert out == {"a": "reply-a", "b": "reply-b"}
    assert set(fake_bridge.closed_handles) == {"worker-1", "worker-2"}
