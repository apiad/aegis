import asyncio
import json
import shutil

import pytest

from aegis.mcp import AegisMCP, mcp_config_json

pytestmark = pytest.mark.skipif(
    shutil.which("claude") is None, reason="claude not on PATH")


@pytest.mark.asyncio
async def test_live_claude_calls_aegis_meta():
    mcp = AegisMCP()
    await mcp.start()
    try:
        argv = [
            "claude", "-p",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--replay-user-messages", "--verbose",
            "--permission-mode", "bypassPermissions",
            "--strict-mcp-config",
            "--mcp-config", mcp_config_json(mcp.url),
        ]
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        msg = {"type": "user", "message": {
            "role": "user",
            "content": "Call the aegis_meta tool, then tell me which "
                       "MCP server you are on."}}
        proc.stdin.write((json.dumps(msg) + "\n").encode())
        await proc.stdin.drain()
        proc.stdin.close()  # signal no more turns; claude exits after result

        out_bytes = await asyncio.wait_for(proc.stdout.read(), timeout=150)
        await asyncio.wait_for(proc.wait(), timeout=10)
        text = out_bytes.decode("utf-8", "replace")
        # the aegis tool was invoked and the briefing came back
        assert "aegis_meta" in text, text[-2000:]
        assert "meta-harness" in text, text[-2000:]
    finally:
        await mcp.stop()
