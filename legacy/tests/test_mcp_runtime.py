import socket

import pytest

from aegis.mcp.runtime import AegisMCP


@pytest.mark.asyncio
async def test_start_serves_then_stop_cleanly():
    mcp = AegisMCP()
    assert mcp.url.startswith("http://127.0.0.1:")
    assert mcp.url.endswith("/mcp/")
    await mcp.start()
    host, port = "127.0.0.1", mcp.port
    with socket.create_connection((host, port), timeout=2):
        pass
    await mcp.stop()
    await mcp.stop()  # idempotent


@pytest.mark.asyncio
async def test_restart_works():
    mcp = AegisMCP()
    await mcp.start()
    await mcp.stop()
    await mcp.start()
    with socket.create_connection(("127.0.0.1", mcp.port), timeout=2):
        pass
    await mcp.stop()
