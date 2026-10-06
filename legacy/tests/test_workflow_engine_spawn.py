"""Slice 1 — ``engine.spawn`` / ``engine.close`` lifecycle."""
from __future__ import annotations

import pytest

from aegis.workflow.engine import WorkflowEngine
from aegis.workflow import SubagentSpawnError


@pytest.mark.asyncio
async def test_spawn_returns_handle(fake_bridge_with_spawner):
    eng = WorkflowEngine(
        bridge=fake_bridge_with_spawner, workflow_id="w", name="t",
        host="lucid-knuth", config={})
    handle = await eng.spawn("implementer")
    assert isinstance(handle, str)
    assert handle in fake_bridge_with_spawner.live_handles


@pytest.mark.asyncio
async def test_spawn_respects_alias(fake_bridge_with_spawner):
    eng = WorkflowEngine(
        bridge=fake_bridge_with_spawner, workflow_id="w", name="t",
        host="lucid-knuth", config={})
    handle = await eng.spawn("implementer", alias="impl-slice-1")
    assert handle == "impl-slice-1"


@pytest.mark.asyncio
async def test_close_subagent_succeeds(fake_bridge_with_spawner):
    eng = WorkflowEngine(
        bridge=fake_bridge_with_spawner, workflow_id="w", name="t",
        host="lucid-knuth", config={})
    handle = await eng.spawn("implementer")
    await eng.close(handle)
    assert handle not in fake_bridge_with_spawner.live_handles


@pytest.mark.asyncio
async def test_close_host_raises(fake_bridge_with_spawner):
    eng = WorkflowEngine(
        bridge=fake_bridge_with_spawner, workflow_id="w", name="t",
        host="lucid-knuth", config={})
    with pytest.raises(ValueError, match="cannot close host"):
        await eng.close(eng.host)


@pytest.mark.asyncio
async def test_close_unknown_handle_is_noop(fake_bridge_with_spawner):
    eng = WorkflowEngine(
        bridge=fake_bridge_with_spawner, workflow_id="w", name="t",
        host="h", config={})
    # Idempotent: no exception when closing a handle we never spawned.
    await eng.close("ghost")


@pytest.mark.asyncio
async def test_spawn_failure_wraps_in_subagent_error(fake_bridge_with_spawner):
    async def boom(*a, **kw):
        raise RuntimeError("upstream blew up")
    fake_bridge_with_spawner.spawn_subagent = boom  # type: ignore[assignment]
    eng = WorkflowEngine(
        bridge=fake_bridge_with_spawner, workflow_id="w", name="t",
        host="h", config={})
    with pytest.raises(SubagentSpawnError, match="upstream blew up"):
        await eng.spawn("implementer")
