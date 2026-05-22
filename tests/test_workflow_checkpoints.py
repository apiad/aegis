"""Slice 3 — engine.checkpoint + engine.resume_state via the runner ledger."""
from __future__ import annotations

import json

import pytest

from aegis.workflow.engine import WorkflowEngine


async def test_checkpoint_appends_ledger(fake_bridge_with_state, tmp_path):
    fake_bridge_with_state.set_state_dir(tmp_path / "wf")
    eng = WorkflowEngine(
        bridge=fake_bridge_with_state, workflow_id="w1", name="t",
        host="h", config={})
    await eng.checkpoint("phase_one", {"x": 1})
    ledger = (tmp_path / "wf" / "w1" / "ledger.jsonl").read_text()
    recs = [json.loads(l) for l in ledger.splitlines() if l.strip()]
    assert any(r["kind"] == "checkpoint" and r["name"] == "phase_one"
               and r["payload"] == {"x": 1} for r in recs)


async def test_resume_state_returns_last_checkpoint_payload(
        fake_bridge_with_state, tmp_path):
    fake_bridge_with_state.set_state_dir(tmp_path / "wf")
    eng = WorkflowEngine(
        bridge=fake_bridge_with_state, workflow_id="w2", name="t",
        host="h", config={})
    await eng.checkpoint("one", {"x": 1})
    await eng.checkpoint("two", {"x": 2})
    eng2 = WorkflowEngine(
        bridge=fake_bridge_with_state, workflow_id="w2", name="t",
        host="h", config={})
    state = await eng2.resume_state()
    assert state == {"x": 2}


async def test_resume_state_returns_none_for_fresh_workflow(
        fake_bridge_with_state, tmp_path):
    fake_bridge_with_state.set_state_dir(tmp_path / "wf")
    eng = WorkflowEngine(
        bridge=fake_bridge_with_state, workflow_id="fresh", name="t",
        host="h", config={})
    assert await eng.resume_state() is None


async def test_non_jsonable_checkpoint_raises_immediately(
        fake_bridge_with_state, tmp_path):
    fake_bridge_with_state.set_state_dir(tmp_path / "wf")
    eng = WorkflowEngine(
        bridge=fake_bridge_with_state, workflow_id="w3", name="t",
        host="h", config={})
    with pytest.raises(TypeError):
        await eng.checkpoint("bad", {"sock": object()})


async def test_checkpoint_no_runner_raises(fake_bridge):
    fake_bridge.workflow_runner = None
    eng = WorkflowEngine(
        bridge=fake_bridge, workflow_id="w", name="t",
        host="h", config={})
    with pytest.raises(RuntimeError, match="workflow_runner"):
        await eng.checkpoint("x", {"a": 1})
