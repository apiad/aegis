"""VS2 proof: a native lovelaice agent, given an aegis-plane MCP server via
the driver's mcp_url, calls a tool on it. Validates per-session MCP injection
(HTTP transport) end-to-end against a real model.

Gated: needs `lovelaice-acp` on PATH and an OpenRouter key file.
"""
import asyncio
import shutil
import socket
from pathlib import Path

import pytest

from aegis.config import Agent, Lovelaice
from aegis.drivers.lovelaice import LovelaiceDriver

TOKEN = "/home/apiad/Workspace/.claude/openrouter.token"

pytestmark = [
    pytest.mark.skipif(shutil.which("lovelaice-acp") is None,
                       reason="lovelaice-acp not on PATH"),
    pytest.mark.skipif(not Path(TOKEN).is_file(), reason="no OpenRouter token"),
]


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.mark.asyncio
async def test_lovelaice_calls_injected_mcp_tool(tmp_path):
    from fastmcp import FastMCP

    port = _free_port()
    received: list[str] = []
    srv = FastMCP("aegis-livetest")

    @srv.tool
    def aegis_claim(paths: str) -> str:
        received.append(paths)
        return f"claimed: {paths}"

    server_task = asyncio.create_task(
        srv.run_http_async(host="127.0.0.1", port=port, show_banner=False))
    for _ in range(50):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            await asyncio.sleep(0.05)

    try:
        agent = Agent(provider=Lovelaice(
            model="anthropic/claude-haiku-4-5",
            base_url="https://openrouter.ai/api/v1",
            api_key_file=TOKEN))
        sess = LovelaiceDriver().session(
            agent, str(tmp_path),
            mcp_url=f"http://127.0.0.1:{port}/mcp", handle="lov-mcp")
        await sess.start()
        await sess.send(
            "Call the MCP tool aegis_claim with paths='src/foo.py'. Then stop.")
        events = [ev async for ev in sess.events()]
        await sess.close()
        assert received == ["src/foo.py"], (
            f"MCP server received {received!r}; events={events!r}")
    finally:
        server_task.cancel()
