"""The ACP half of the live round-trip.

`test_identity_live.py` proves the header survives a real `claude`
subprocess. That says nothing about the other harness family: ACP carries
the token in a different field, of a different shape
(`List[HttpHeader]` — `{name, value}` — rather than a mapping), and a dict
there is accepted by aegis's own dict-shaped code and dropped downstream.
Only a real ACP agent calling a real server can tell us it arrived.

Gated exactly like `test_lovelaice_mcp_live.py`: needs `lovelaice-acp` on
PATH and an OpenRouter key. Note that `lovelaice-acp` ships in aegis's own
venv but is not necessarily on the outer PATH — run with
`PATH="$PWD/.venv/bin:$PATH"` if it skips.
"""
import asyncio
import shutil
import socket
from pathlib import Path

import pytest

from aegis.config import Agent, Lovelaice
from aegis.drivers.lovelaice import LovelaiceDriver
from aegis.mcp.identity import SessionTokens, resolve_caller

TOKEN = "/home/apiad/Workspace/.claude/openrouter.token"

pytestmark = [
    pytest.mark.live,
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
async def test_a_real_acp_agent_carries_the_token_to_a_real_server(tmp_path):
    from fastmcp import FastMCP

    tokens = SessionTokens()
    token = tokens.mint("alice")
    seen: list[str | None] = []

    port = _free_port()
    srv = FastMCP("identity-acp-live")

    @srv.tool
    def report_caller() -> str:
        """Record who aegis thinks is calling. Call this tool once."""
        who = resolve_caller(tokens)
        seen.append(who)
        return who or "(unattributed)"

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
            mcp_url=f"http://127.0.0.1:{port}/mcp", handle="lov-identity",
            token=token)
        await sess.start()
        await sess.send(
            "Call the MCP tool report_caller with no arguments. Then stop.")
        events = [ev async for ev in sess.events()]
        await sess.close()

        assert seen, (
            f"the agent never called the tool — MCP attach failed; "
            f"events={events!r}")
        assert seen[0] == "alice", f"header did not survive: got {seen[0]!r}"
    finally:
        server_task.cancel()
