"""Live smoke tests for the Gemini and OpenCode drivers (ACP-based).

Three checks per provider against the real CLI subprocess:

1. **Round-trip** — single prompt → some assistant text + success
   Result. Text aggregated across all AssistantText events
   (OpenCode streams token-by-token).
2. **Multi-turn memory** — two prompts on the same session; second
   recalls fact from first.
3. **Per-session MCP injection** — spawn a tiny FastMCP server, hand
   its URL to the driver via ``mcp_url``, ask the agent to call the
   tool, assert our MCP server received the call.

Each test auto-skips when the relevant CLI isn't on PATH.
"""
from __future__ import annotations

import asyncio
import shutil
import socket

import pytest

from aegis import Agent, GeminiCLI, OpenCode
from aegis.drivers.gemini import GeminiDriver
from aegis.drivers.opencode import OpenCodeDriver
from aegis.events import AssistantText, Result


_HAVE_GEMINI = shutil.which("gemini") is not None
_HAVE_OPENCODE = shutil.which("opencode") is not None


pytestmark = pytest.mark.live


def _aggregate_text(events) -> str:
    return "".join(e.text for e in events if isinstance(e, AssistantText))


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# ---------- Gemini --------------------------------------------------


@pytest.mark.skipif(not _HAVE_GEMINI, reason="gemini CLI not on PATH")
async def test_gemini_driver_round_trip(tmp_path):
    """Single prompt → aggregated AssistantText containing PING + ok Result."""
    agent = Agent(provider=GeminiCLI(
        model="gemini-3-flash-preview", permission="full"))
    sess = GeminiDriver().session(agent, str(tmp_path),
                                   mcp_url="", handle="g1")
    await sess.start()
    await sess.send("Reply with the single word PING and stop.")
    events = [ev async for ev in sess.events()]
    await sess.close()

    text = _aggregate_text(events)
    assert "PING" in text.upper(), text
    result = next((e for e in events if isinstance(e, Result)), None)
    assert result is not None and not result.is_error


@pytest.mark.skipif(not _HAVE_GEMINI, reason="gemini CLI not on PATH")
async def test_gemini_multi_turn_memory(tmp_path):
    """Two prompts in one session; second recalls fact from first."""
    agent = Agent(provider=GeminiCLI(
        model="gemini-3-flash-preview", permission="full"))
    sess = GeminiDriver().session(agent, str(tmp_path),
                                   mcp_url="", handle="g2")
    await sess.start()

    await sess.send("Remember the number 4217. Reply with just OK.")
    turn1 = [ev async for ev in sess.events()]
    assert "OK" in _aggregate_text(turn1).upper()

    await sess.send("What number did I ask you to remember? "
                    "Reply with just the digits.")
    turn2 = [ev async for ev in sess.events()]
    await sess.close()
    assert "4217" in _aggregate_text(turn2)


@pytest.mark.skipif(not _HAVE_GEMINI, reason="gemini CLI not on PATH")
async def test_gemini_mcp_per_session_injection(tmp_path):
    """Spin up a one-tool FastMCP server, hand its URL to the driver
    via mcp_url, ask gemini to call the tool, assert our server
    received the call. Validates the canonical feature-parity claim."""
    from fastmcp import FastMCP

    port = _free_port()
    received: list[str] = []
    srv = FastMCP("aegis-livetest")

    @srv.tool
    def aegis_ping(message: str) -> str:
        received.append(message)
        return f"pong: {message}"

    server_task = asyncio.create_task(
        srv.run_http_async(host="127.0.0.1", port=port,
                           show_banner=False))
    for _ in range(50):
        try:
            with socket.create_connection(
                    ("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            await asyncio.sleep(0.05)

    try:
        agent = Agent(provider=GeminiCLI(
            model="gemini-3-flash-preview", permission="full"))
        sess = GeminiDriver().session(
            agent, str(tmp_path),
            mcp_url=f"http://127.0.0.1:{port}/mcp", handle="g3")
        await sess.start()
        await sess.send(
            "Call the MCP tool aegis_ping with message='hello-gemini'. "
            "Then stop.")
        events = [ev async for ev in sess.events()]
        await sess.close()
        assert received == ["hello-gemini"], (
            f"MCP server received {received!r}; events={events!r}")
    finally:
        server_task.cancel()


# ---------- OpenCode ------------------------------------------------


@pytest.mark.skipif(not _HAVE_OPENCODE, reason="opencode CLI not on PATH")
async def test_opencode_driver_round_trip(tmp_path):
    agent = Agent(provider=OpenCode(
        model="opencode/claude-haiku-4-5", permission="full"))
    sess = OpenCodeDriver().session(agent, str(tmp_path),
                                     mcp_url="", handle="o1")
    await sess.start()
    await sess.send("Reply with the single word PONG and stop.")
    events = [ev async for ev in sess.events()]
    await sess.close()

    text = _aggregate_text(events)
    assert "PONG" in text.upper(), text
    result = next((e for e in events if isinstance(e, Result)), None)
    assert result is not None and not result.is_error


@pytest.mark.skipif(not _HAVE_OPENCODE, reason="opencode CLI not on PATH")
async def test_opencode_multi_turn_memory(tmp_path):
    agent = Agent(provider=OpenCode(
        model="opencode/claude-haiku-4-5", permission="full"))
    sess = OpenCodeDriver().session(agent, str(tmp_path),
                                     mcp_url="", handle="o2")
    await sess.start()

    await sess.send("Remember the number 4217. Reply with just OK.")
    turn1 = [ev async for ev in sess.events()]
    assert "OK" in _aggregate_text(turn1).upper()

    await sess.send("What number did I ask you to remember? "
                    "Reply with just the digits.")
    turn2 = [ev async for ev in sess.events()]
    await sess.close()
    assert "4217" in _aggregate_text(turn2)


@pytest.mark.skipif(not _HAVE_OPENCODE, reason="opencode CLI not on PATH")
async def test_opencode_mcp_per_session_injection(tmp_path):
    """Same proof as the gemini variant, against opencode."""
    from fastmcp import FastMCP

    port = _free_port()
    received: list[str] = []
    srv = FastMCP("aegis-livetest")

    @srv.tool
    def aegis_ping(message: str) -> str:
        received.append(message)
        return f"pong: {message}"

    server_task = asyncio.create_task(
        srv.run_http_async(host="127.0.0.1", port=port,
                           show_banner=False))
    for _ in range(50):
        try:
            with socket.create_connection(
                    ("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            await asyncio.sleep(0.05)

    try:
        agent = Agent(provider=OpenCode(
            model="opencode/claude-haiku-4-5", permission="full"))
        sess = OpenCodeDriver().session(
            agent, str(tmp_path),
            mcp_url=f"http://127.0.0.1:{port}/mcp", handle="o3")
        await sess.start()
        await sess.send(
            "Call the MCP tool aegis_ping with message='hello-opencode'. "
            "Then stop.")
        events = [ev async for ev in sess.events()]
        await sess.close()
        assert received == ["hello-opencode"], (
            f"MCP server received {received!r}; events={events!r}")
    finally:
        server_task.cancel()
