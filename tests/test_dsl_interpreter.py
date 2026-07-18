from __future__ import annotations

import json

import pytest

from aegis.dsl.interpreter import dynamic
from aegis.workflow.decorator import WorkflowError
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


async def test_agent_inputs_substituted_and_output_referenceable(fake_bridge):
    fake_bridge.set_reply_sequence(
        "lister-1", [json.dumps({"files": ["a.ts", "b.ts"]})])
    fake_bridge.set_reply_sequence("merger-2", ["merged"])
    spec = {
        "meta": {"name": "s"},
        "root": {"type": "sequence", "children": [
            {"type": "agent", "id": "list", "prompt": "list files",
             "target": {"kind": "spawn", "profile": "lister"},
             "schema": {"type": "object", "required": ["files"],
                        "properties": {"files": {"type": "array"}}}},
            {"type": "agent", "id": "report", "prompt": "merge {{all}}",
             "target": {"kind": "spawn", "profile": "merger"},
             "inputs": {"all": "list"}},
        ]},
    }
    out = await dynamic(_engine(fake_bridge), spec=spec)
    assert out["list"] == {"files": ["a.ts", "b.ts"]}
    assert "a.ts" in fake_bridge.sends_to("merger-2")[0]


async def test_agent_schema_violation_after_retry_raises(fake_bridge):
    fake_bridge.set_reply_sequence("w-1", ["not json", "still not json"])
    spec = {"meta": {"name": "s"},
            "root": {"type": "agent", "id": "x", "prompt": "p",
                     "target": {"kind": "spawn", "profile": "w"},
                     "schema": {"type": "object", "required": ["k"],
                                "properties": {"k": {"type": "string"}}}}}
    with pytest.raises(WorkflowError):
        await dynamic(_engine(fake_bridge), spec=spec)
