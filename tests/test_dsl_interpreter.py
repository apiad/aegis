from __future__ import annotations

import json

import pytest

from aegis.dsl.interpreter import Interpreter, dynamic
from aegis.dsl.models import JudgePredicate, ShellPredicate
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


async def test_map_fans_out_over_list(fake_bridge):
    fake_bridge.set_reply_sequence(
        "lister-1", [json.dumps({"files": ["a.ts", "b.ts"]})])
    fake_bridge.set_reply_sequence("auditor-2", ["found in a"])
    fake_bridge.set_reply_sequence("auditor-3", ["found in b"])
    spec = {"meta": {"name": "s"}, "root": {"type": "sequence", "children": [
        {"type": "agent", "id": "list", "prompt": "list",
         "target": {"kind": "spawn", "profile": "lister"},
         "schema": {"type": "object", "properties": {"files": {"type": "array"}}}},
        {"type": "map", "id": "audits", "over": "list.files", "concurrency": 2,
         "body": {"type": "agent", "prompt": "audit {{item}} idx {{index}}",
                  "target": {"kind": "spawn", "profile": "auditor"}}}]}}
    out = await dynamic(_engine(fake_bridge), spec=spec)
    assert out["audits"] == ["found in a", "found in b"]
    prompts = fake_bridge.sends_to("auditor-2") + fake_bridge.sends_to("auditor-3")
    assert any("audit a.ts idx 0" in p for p in prompts)
    assert any("audit b.ts idx 1" in p for p in prompts)


async def test_shell_predicate_exit0_true_exit1_false(fake_bridge):
    fake_bridge.set_bash_sequence([{"exit": 0, "stdout": "", "stderr": ""},
                                   {"exit": 1, "stdout": "", "stderr": ""}])
    interp = Interpreter(_engine(fake_bridge), args={}, default_profile="w")
    pred = ShellPredicate(cmd="tsc --noEmit")
    assert await interp._eval_predicate(
        pred, path="root::pred", scope={}, last=None) is True
    assert await interp._eval_predicate(
        pred, path="root::pred", scope={}, last=None) is False


async def test_judge_predicate_returns_decision(fake_bridge):
    fake_bridge.set_reply_sequence(
        "w-1", [json.dumps({"decision": True, "reason": "ok"})])
    interp = Interpreter(_engine(fake_bridge), args={}, default_profile="w")
    pred = JudgePredicate(condition="is it green?", inputs=[])
    assert await interp._eval_predicate(
        pred, path="root::pred", scope={}, last="green") is True


async def test_parallel_runs_children_and_keyed_by_id(fake_bridge):
    fake_bridge.set_reply_sequence("w-1", ["A"])
    fake_bridge.set_reply_sequence("w-2", ["B"])
    spec = {"meta": {"name": "s"}, "root": {
        "type": "parallel", "children": [
            {"type": "agent", "id": "x", "prompt": "px",
             "target": {"kind": "spawn", "profile": "w"}},
            {"type": "agent", "id": "y", "prompt": "py",
             "target": {"kind": "spawn", "profile": "w"}},
        ]}}
    out = await dynamic(_engine(fake_bridge), spec=spec)
    assert out == {"x": "A", "y": "B"}
