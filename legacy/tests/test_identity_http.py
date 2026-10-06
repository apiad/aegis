from __future__ import annotations

import asyncio
import contextlib
import socket

import pytest
from fastmcp import Client, FastMCP
from fastmcp.client.transports import StreamableHttpTransport

from aegis.mcp.identity import HEADER, SessionTokens, resolve_caller


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@contextlib.asynccontextmanager
async def _serving(tokens: SessionTokens):
    """A real HTTP FastMCP server on a free port.

    Deliberately NOT the in-memory transport: `Client(server)` takes no
    `headers`, so an in-memory round-trip cannot observe the one thing
    this task exists to prove. The readiness loop mirrors
    `AegisMCP.start` (`mcp/runtime.py`).
    """
    server = FastMCP("identity-probe")

    @server.tool
    async def whoami() -> str:
        """Report the resolved caller, or the empty string."""
        return resolve_caller(tokens) or ""

    port = _free_port()
    task = asyncio.create_task(
        server.run_http_async(host="127.0.0.1", port=port, show_banner=False))
    try:
        for _ in range(100):  # ~5s max
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                await asyncio.sleep(0.05)
        else:
            raise RuntimeError("probe server did not start")
        yield f"http://127.0.0.1:{port}/mcp/"
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


async def _whoami(url: str, headers: dict | None) -> str:
    """Headers ride the TRANSPORT, not the Client.

    `Client.__init__` (FastMCP 3.2.0) has no `headers` parameter at all —
    passing one is a TypeError, not a silently ignored kwarg, which is the
    one merciful thing about it.
    """
    async with Client(StreamableHttpTransport(url, headers=headers)) as client:
        out = await client.call_tool("whoami", {})
    return out.data


@pytest.mark.asyncio
async def test_the_header_reaches_the_server_and_resolves():
    tokens = SessionTokens()
    token = tokens.mint("alice")
    async with _serving(tokens) as url:
        assert await _whoami(url, {HEADER: token}) == "alice"


@pytest.mark.asyncio
async def test_a_capitalised_header_still_resolves():
    """HTTP header names are case-insensitive; the lookup must be too."""
    tokens = SessionTokens()
    token = tokens.mint("alice")
    async with _serving(tokens) as url:
        assert await _whoami(url, {"X-Aegis-Session": token}) == "alice"


@pytest.mark.asyncio
async def test_no_header_resolves_to_nothing_rather_than_failing():
    tokens = SessionTokens()
    tokens.mint("alice")
    async with _serving(tokens) as url:
        assert await _whoami(url, None) == ""


@pytest.mark.asyncio
async def test_a_stale_token_resolves_to_nothing():
    tokens = SessionTokens()
    stale = tokens.mint("alice")
    tokens.revoke("alice")
    async with _serving(tokens) as url:
        assert await _whoami(url, {HEADER: stale}) == ""
