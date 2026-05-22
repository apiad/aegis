"""Slice 2 — non-blocking MCP trigger + status/cancel tools."""
from __future__ import annotations

import asyncio

import pytest

from aegis.mcp.server import build_server
from aegis.workflow import workflow
from aegis.workflow.decorator import _REGISTRY


@pytest.fixture(autouse=True)
def _clean_registry():
    _REGISTRY.clear()
    yield
    _REGISTRY.clear()


async def _call(server, name, args):
    tools = await server.list_tools()
    tool = next(t for t in tools if t.name == name)
    return await tool.run(args)


async def test_run_workflow_returns_immediately(fake_bridge_with_runner):
    @workflow
    async def echo(engine, *, text=""):
        return text or "ok"

    server = build_server(fake_bridge_with_runner)
    res = await _call(server, "aegis_run_workflow", {
        "name": "echo", "kwargs": {"text": "hi"},
        "from_handle": "lucid-knuth"})
    data = res.structured_content
    assert "workflow_id" in data
    assert data["host"] == "lucid-knuth"
    assert data["status"] == "running"


async def test_workflow_status_returns_state(fake_bridge_with_runner):
    @workflow
    async def quick(engine):
        return "done"

    server = build_server(fake_bridge_with_runner)
    r1 = await _call(server, "aegis_run_workflow", {
        "name": "quick", "kwargs": {}, "from_handle": "h"})
    wid = r1.structured_content["workflow_id"]
    for _ in range(20):
        await asyncio.sleep(0.01)
        rs = await _call(server, "aegis_workflow_status",
                         {"workflow_id": wid})
        if rs.structured_content["status"] != "running":
            break
    data = rs.structured_content
    assert data["workflow_id"] == wid
    assert data["status"] == "ok"
    assert data.get("result") == "done"


async def test_workflow_cancel_terminates_task(fake_bridge_with_runner):
    @workflow
    async def long_sleeper(engine):
        await asyncio.sleep(60)
        return "should-not-arrive"

    server = build_server(fake_bridge_with_runner)
    r1 = await _call(server, "aegis_run_workflow", {
        "name": "long_sleeper", "kwargs": {}, "from_handle": "h"})
    wid = r1.structured_content["workflow_id"]
    await asyncio.sleep(0.02)
    r2 = await _call(server, "aegis_workflow_cancel", {"workflow_id": wid})
    data = r2.structured_content
    assert data["ok"] is True


async def test_workflow_status_unknown_id(fake_bridge_with_runner):
    server = build_server(fake_bridge_with_runner)
    rs = await _call(server, "aegis_workflow_status",
                     {"workflow_id": "wf_nope"})
    assert rs.structured_content["status"] == "unknown"
