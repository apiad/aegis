"""The ledger's attribution, end to end, with nothing mocked.

`test_identity_ledger.py` proves `CommsMiddleware` resolves a caller, but it
fakes the request context with a monkeypatched `caller_token`. That leaves the
seam this feature actually rests on untested: a real header, arriving on a real
request, reaching the middleware that `build_server` mounted, and landing in
the ledger as the `from` a human reads in `aegis comms list`.

Kept apart from `test_identity_ledger.py` because it needs an HTTP server and
is therefore slower — the unit file stays fast and this one stays honest.
"""
from __future__ import annotations

import asyncio
import contextlib
import socket
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from aegis.comms.persistence import CommsLedger
from aegis.mcp.identity import HEADER, SessionTokens
from aegis.mcp.server import build_server
from tests.test_mcp_server import FakeBridge


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@contextlib.asynccontextmanager
async def _serving(server):
    port = _free_port()
    task = asyncio.create_task(
        server.run_http_async(host="127.0.0.1", port=port, show_banner=False))
    try:
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                await asyncio.sleep(0.05)
        else:
            raise RuntimeError("server did not start")
        yield f"http://127.0.0.1:{port}/mcp/"
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


async def _call_with_header(url: str, token: str | None, tool: str, **args):
    headers = {HEADER: token} if token else None
    async with Client(StreamableHttpTransport(url, headers=headers)) as c:
        await c.call_tool(tool, args)


def _ledger_rows() -> list[dict]:
    # build_server resolves the ledger to cwd/.aegis/state when the bridge
    # has no queue manager; the autouse isolated_project_dir fixture makes
    # that a per-test tmp dir.
    return CommsLedger(Path.cwd() / ".aegis" / "state").read_all()


@pytest.mark.asyncio
async def test_a_real_header_attributes_a_tool_that_takes_no_from_handle():
    """The headline payoff: `aegis comms list` stops printing
    (unattributed) for the whole no-parameter family."""
    tokens = SessionTokens()
    token = tokens.mint("alice")
    async with _serving(build_server(FakeBridge(), tokens=tokens)) as url:
        await _call_with_header(url, token, "aegis_list_sessions")

    rows = [r for r in _ledger_rows() if r["verb"] == "list_sessions"]
    assert [r["from"] for r in rows] == ["alice"]


@pytest.mark.asyncio
async def test_a_real_header_beats_a_wrong_from_handle_argument():
    tokens = SessionTokens()
    token = tokens.mint("alice")
    async with _serving(build_server(FakeBridge(), tokens=tokens)) as url:
        await _call_with_header(url, token, "aegis_list_agents")

    rows = [r for r in _ledger_rows() if r["verb"] == "list_agents"]
    assert [r["from"] for r in rows] == ["alice"]


@pytest.mark.asyncio
async def test_no_header_still_records_the_row_unattributed():
    """v1 refuses nothing: an out-of-band caller is recorded honestly
    rather than rejected or guessed at."""
    tokens = SessionTokens()
    tokens.mint("alice")
    async with _serving(build_server(FakeBridge(), tokens=tokens)) as url:
        await _call_with_header(url, None, "aegis_list_sessions")

    rows = [r for r in _ledger_rows() if r["verb"] == "list_sessions"]
    assert [r["from"] for r in rows] == [""]
