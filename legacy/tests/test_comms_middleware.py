"""Every call through the MCP surface leaves exactly one envelope."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastmcp import Client, FastMCP

from aegis.comms.middleware import CommsMiddleware
from aegis.comms.persistence import CommsLedger


def _server(ledger: CommsLedger) -> FastMCP:
    server = FastMCP("test")
    server.add_middleware(CommsMiddleware(ledger))

    @server.tool
    async def aegis_enqueue(queue: str, payload: str,
                            from_handle: str) -> dict:
        return {"task_id": "01TASK", "queued_position": 2}

    @server.tool
    async def aegis_handoff(from_handle: str, target_handle: str,
                            context: str) -> dict:
        return {"result": "landed"}

    @server.tool
    async def aegis_list_sessions() -> list[dict]:
        return []

    @server.tool
    async def aegis_claim(paths: list[str], from_handle: str) -> dict:
        raise ValueError("denied")

    return server


async def _call(server: FastMCP, name: str, args: dict) -> None:
    async with Client(server) as client:
        await client.call_tool(name, args)


def test_a_successful_call_writes_one_ok_envelope(tmp_path: Path):
    ledger = CommsLedger(tmp_path)
    asyncio.run(_call(_server(ledger), "aegis_handoff", {
        "from_handle": "me", "target_handle": "weary-turing",
        "context": "the render is yours"}))
    rows = ledger.read_all()
    assert len(rows) == 1
    assert rows[0]["verb"] == "handoff"
    assert rows[0]["from"] == "me"
    assert rows[0]["to"] == {"kind": "agent", "id": "weary-turing"}
    assert rows[0]["family"] == "conversation"
    assert rows[0]["outcome"] == "ok"
    assert rows[0]["duration_ms"] >= 0


def test_the_thread_is_the_substrate_id_from_the_result(tmp_path: Path):
    ledger = CommsLedger(tmp_path)
    asyncio.run(_call(_server(ledger), "aegis_enqueue", {
        "queue": "general", "payload": "port the fixtures",
        "from_handle": "me"}))
    row = ledger.read_all()[0]
    assert row["thread"] == "01TASK"
    assert row["thread"] != row["call_id"]


def test_a_call_with_no_substrate_id_threads_on_its_own_call_id(tmp_path):
    ledger = CommsLedger(tmp_path)
    asyncio.run(_call(_server(ledger), "aegis_handoff", {
        "from_handle": "me", "target_handle": "p", "context": "x"}))
    row = ledger.read_all()[0]
    assert row["thread"] == row["call_id"]


def test_a_failing_tool_still_leaves_an_error_envelope(tmp_path: Path):
    ledger = CommsLedger(tmp_path)
    with pytest.raises(Exception):
        asyncio.run(_call(_server(ledger), "aegis_claim", {
            "paths": ["src/"], "from_handle": "me"}))
    rows = ledger.read_all()
    assert len(rows) == 1
    assert rows[0]["verb"] == "claim"
    assert rows[0]["outcome"] == "error"


def test_a_tool_without_from_handle_is_recorded_unattributed(tmp_path: Path):
    ledger = CommsLedger(tmp_path)
    asyncio.run(_call(_server(ledger), "aegis_list_sessions", {}))
    row = ledger.read_all()[0]
    assert row["from"] == ""
    assert row["to"] is None
    assert row["family"] == "introspection"


def test_a_broken_ledger_never_fails_the_tool(tmp_path: Path):
    """Observability that can break what it observes is a liability."""
    class Exploding(CommsLedger):
        def write(self, env):  # noqa: ANN001
            raise OSError("disk is gone")

    server = _server(Exploding(tmp_path))

    async def run() -> object:
        async with Client(server) as client:
            return await client.call_tool("aegis_handoff", {
                "from_handle": "me", "target_handle": "p", "context": "x"})

    result = asyncio.run(run())
    assert result is not None


def test_the_middleware_ignores_tools_that_are_not_ours(tmp_path: Path):
    ledger = CommsLedger(tmp_path)
    server = FastMCP("test")
    server.add_middleware(CommsMiddleware(ledger))

    @server.tool
    async def some_plugin_tool(x: str) -> str:
        return x

    asyncio.run(_call(server, "some_plugin_tool", {"x": "hi"}))
    assert ledger.read_all() == []
