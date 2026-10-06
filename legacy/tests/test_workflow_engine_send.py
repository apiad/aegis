"""Slice 1 — unified ``engine.send`` returning the reply text.

Covers the new send shape introduced by the workflow-catalog v1
extension. Legacy fire-and-forget tests live in
``tests/test_workflow_engine.py``.
"""
from __future__ import annotations

import pytest

from aegis.workflow.engine import WorkflowEngine


@pytest.mark.asyncio
async def test_engine_has_host_workflow_id_name_config(fake_bridge):
    eng = WorkflowEngine(
        bridge=fake_bridge, workflow_id="wf_1", name="t",
        host="lucid-knuth", config={"k": 1})
    assert eng.host == "lucid-knuth"
    assert eng.workflow_id == "wf_1"
    assert eng.name == "t"
    assert eng.config == {"k": 1}
    # Legacy alias attributes still resolve.
    assert eng.workflow_name == "t"
    assert eng.workflow_run_id == "wf_1"
    assert eng.caller_handle == "lucid-knuth"


@pytest.mark.asyncio
async def test_send_to_host_returns_reply(fake_bridge_with_canned_reply):
    fake_bridge_with_canned_reply.set_reply("lucid-knuth", "ack: hi")
    eng = WorkflowEngine(
        bridge=fake_bridge_with_canned_reply, workflow_id="w",
        name="t", host="lucid-knuth", config={})
    reply = await eng.send(eng.host, "hi")
    assert reply == "ack: hi"
    assert fake_bridge_with_canned_reply.sends_to("lucid-knuth") == ["hi"]


@pytest.mark.asyncio
async def test_send_to_subagent_returns_reply(fake_bridge_with_canned_reply):
    fake_bridge_with_canned_reply.set_reply("brisk-curie", "subagent reply")
    eng = WorkflowEngine(
        bridge=fake_bridge_with_canned_reply, workflow_id="w",
        name="t", host="lucid-knuth", config={})
    reply = await eng.send("brisk-curie", "do it")
    assert reply == "subagent reply"


@pytest.mark.asyncio
async def test_send_records_touched_handle(fake_bridge_with_canned_reply):
    eng = WorkflowEngine(
        bridge=fake_bridge_with_canned_reply, workflow_id="w",
        name="t", host="h", config={})
    await eng.send("brisk-curie", "do it")
    assert "brisk-curie" in eng._touched_handles


def test_engine_requires_name_and_workflow_id(fake_bridge):
    with pytest.raises(TypeError):
        WorkflowEngine(bridge=fake_bridge, workflow_id="w", host="h")
    with pytest.raises(TypeError):
        WorkflowEngine(bridge=fake_bridge, name="t", host="h")


def test_engine_legacy_kwargs_still_work(fake_bridge):
    eng = WorkflowEngine(
        bridge=fake_bridge, workflow_name="t",
        workflow_run_id="w", caller_handle="h")
    assert eng.name == "t"
    assert eng.workflow_id == "w"
    assert eng.host == "h"
    assert eng.config == {}
