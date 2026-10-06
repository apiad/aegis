"""Slice 2 — engine.ask_human() routes through the workflow_runner."""
from __future__ import annotations

import asyncio

import pytest

from aegis.workflow.engine import WorkflowEngine


async def test_ask_human_returns_user_reply(fake_bridge_with_human_queue):
    fake_bridge_with_human_queue.enqueue_reply("lucid-knuth", "the user said this")
    eng = WorkflowEngine(
        bridge=fake_bridge_with_human_queue, workflow_id="w", name="t",
        host="lucid-knuth", config={})
    reply = await eng.ask_human("what color?")
    assert reply == "the user said this"


async def test_ask_human_with_options(fake_bridge_with_human_queue):
    fake_bridge_with_human_queue.enqueue_reply("lucid-knuth", "red")
    eng = WorkflowEngine(
        bridge=fake_bridge_with_human_queue, workflow_id="w", name="t",
        host="lucid-knuth", config={})
    reply = await eng.ask_human("which?", options=["red", "blue", "green"])
    assert reply == "red"
    assert fake_bridge_with_human_queue.last_options("lucid-knuth") == [
        "red", "blue", "green"]


async def test_ask_human_fifo_when_two_questions_queued(
        fake_bridge_with_human_queue):
    fake_bridge_with_human_queue.enqueue_reply("lucid-knuth", "first")
    fake_bridge_with_human_queue.enqueue_reply("lucid-knuth", "second")
    eng = WorkflowEngine(
        bridge=fake_bridge_with_human_queue, workflow_id="w", name="t",
        host="lucid-knuth", config={})
    assert await eng.ask_human("q1") == "first"
    assert await eng.ask_human("q2") == "second"


async def test_ask_human_no_runner_raises(fake_bridge):
    fake_bridge.workflow_runner = None
    eng = WorkflowEngine(
        bridge=fake_bridge, workflow_id="w", name="t",
        host="h", config={})
    with pytest.raises(RuntimeError, match="workflow_runner"):
        await eng.ask_human("x")


async def test_ask_human_times_out(fake_bridge_with_human_queue):
    eng = WorkflowEngine(
        bridge=fake_bridge_with_human_queue, workflow_id="w", name="t",
        host="lucid-knuth", config={})
    with pytest.raises(asyncio.TimeoutError):
        await eng.ask_human("never answered", timeout=0.05)
