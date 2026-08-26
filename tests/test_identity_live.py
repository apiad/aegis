from __future__ import annotations

import shutil

import pytest

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(shutil.which("claude") is None,
                       reason="claude CLI not on PATH"),
]


@pytest.mark.asyncio
async def test_a_real_claude_carries_the_token_to_a_real_server(tmp_path):
    """Everything else in this feature can be green while the header is
    dropped somewhere between the config file and the request. This is
    the only test that would notice."""
    import asyncio
    import socket

    from fastmcp import FastMCP

    from aegis.mcp.identity import SessionTokens, resolve_caller

    tokens = SessionTokens()
    token = tokens.mint("alice")
    seen: list[str | None] = []

    server = FastMCP("identity-live")

    @server.tool
    async def report_caller() -> str:
        """Record who aegis thinks is calling. Call this tool once."""
        who = resolve_caller(tokens)
        seen.append(who)
        return who or "(unattributed)"

    # A free port rather than a fixed one: a hardcoded 8765 collides with
    # whatever else is on this dev box and fails as a timeout, which reads
    # exactly like the header being dropped.
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    task = asyncio.create_task(
        server.run_http_async(host="127.0.0.1", port=port,
                              show_banner=False))
    try:
        for _ in range(100):  # ~5s max
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                await asyncio.sleep(0.05)
        else:
            raise RuntimeError("probe server did not start")

        from aegis.mcp import mcp_config_json
        proc = await asyncio.create_subprocess_exec(
            "claude", "-p", "--output-format", "json",
            "--mcp-config", mcp_config_json(
                f"http://127.0.0.1:{port}/mcp/", token),
            "--strict-mcp-config",
            "--permission-mode", "bypassPermissions",
            "Call the report_caller tool exactly once, then stop.",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(tmp_path))
        out, err = await asyncio.wait_for(proc.communicate(), timeout=180)
    finally:
        task.cancel()

    assert seen, (
        "claude never called the tool — the MCP attach failed.\n"
        f"stdout: {out[-2000:]!r}\nstderr: {err[-2000:]!r}")
    assert seen[0] == "alice", f"header did not survive: got {seen[0]!r}"
