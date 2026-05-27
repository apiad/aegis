import asyncio
import json
import shutil

import pytest

from aegis.mcp import AegisMCP, mcp_config_json
from aegis.mcp.bridge import SessionInfo

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(shutil.which("claude") is None,
                       reason="claude not on PATH"),
]


class _OneSession:
    def list_sessions(self):
        return [SessionInfo("lucid-knuth", "default", "ready", True, False)]

    def list_agents(self):
        return ["default"]

    async def handoff(self, a, b, c):
        return f"delivered to {b}"


@pytest.mark.asyncio
async def test_live_claude_calls_aegis_meta():
    mcp = AegisMCP()
    mcp.bind(_OneSession())
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


class _ConfigEditBridge(_OneSession):
    """Adds the three register hooks the config-edit tools call."""

    def __init__(self) -> None:
        self.registered_queues: list = []

    def register_agent(self, slug, agent): pass
    def register_queue(self, queue): self.registered_queues.append(queue)
    def reload_plugins(self): pass


@pytest.mark.asyncio
async def test_live_agent_calls_aegis_config_add_queue(tmp_path, monkeypatch):
    """End-to-end: a claude -p worker calls aegis_config_add_queue via
    the strict MCP plane; YAML gets updated and the live registry sees
    the new queue."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n"
        "  researcher:\n    provider: claude-code\n    model: opus\n"
        "default_agent: researcher\n"
    )
    bridge = _ConfigEditBridge()
    mcp = AegisMCP()
    mcp.bind(bridge)
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
            cwd=str(tmp_path),
        )
        msg = {"type": "user", "message": {
            "role": "user",
            "content": ("Call aegis_config_add_queue with name='designs', "
                        "agent='researcher', max_parallel=1. Report the "
                        "returned `live` field.")}}
        proc.stdin.write((json.dumps(msg) + "\n").encode())
        await proc.stdin.drain()
        proc.stdin.close()

        out_bytes = await asyncio.wait_for(proc.stdout.read(), timeout=150)
        await asyncio.wait_for(proc.wait(), timeout=10)
        text = out_bytes.decode("utf-8", "replace")
        assert "aegis_config_add_queue" in text, text[-2000:]
        yml = (tmp_path / ".aegis.yaml").read_text()
        assert "designs:" in yml
        assert len(bridge.registered_queues) == 1
        assert bridge.registered_queues[0].name == "designs"
    finally:
        await mcp.stop()


@pytest.mark.asyncio
async def test_live_agent_lists_sessions():
    """An agent should be able to call aegis_list_sessions via the
    strict MCP plane and see a handle in the result."""
    mcp = AegisMCP()
    mcp.bind(_OneSession())
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
            "content": "Call the aegis_list_sessions tool and report "
                       "the handles you see."}}
        proc.stdin.write((json.dumps(msg) + "\n").encode())
        await proc.stdin.drain()
        proc.stdin.close()

        out_bytes = await asyncio.wait_for(proc.stdout.read(), timeout=150)
        await asyncio.wait_for(proc.wait(), timeout=10)
        text = out_bytes.decode("utf-8", "replace")
        assert "aegis_list_sessions" in text, text[-2000:]
        assert "lucid-knuth" in text, text[-2000:]
    finally:
        await mcp.stop()
