"""Full MCP-plane proof with per-stage timeouts + progress prints."""
import asyncio
import socket
import tempfile
import threading
import time
from pathlib import Path

from aegis.config import Agent, Lovelaice
from aegis.drivers.lovelaice import LovelaiceDriver

TOKEN = "/home/apiad/Workspace/.claude/openrouter.token"


def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def _serve(srv, port):
    loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
    loop.run_until_complete(srv.run_http_async(host="127.0.0.1", port=port, show_banner=False))


async def main():
    from fastmcp import FastMCP
    port = _free_port()
    received = []
    srv = FastMCP("aegis-livetest")

    @srv.tool
    def aegis_claim(paths: str) -> str:
        received.append(paths)
        return f"claimed: {paths}"

    threading.Thread(target=_serve, args=(srv, port), daemon=True).start()
    for _ in range(100):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.05)
    print("server up on", port)

    tmp = Path(tempfile.mkdtemp(prefix="lov-mcp-"))
    agent = Agent(provider=Lovelaice(
        model="anthropic/claude-haiku-4-5",
        base_url="https://openrouter.ai/api/v1", api_key_file=TOKEN))
    sess = LovelaiceDriver().session(
        agent, str(tmp), mcp_url=f"http://127.0.0.1:{port}/mcp", handle="lov-mcp")

    print("starting session...")
    await asyncio.wait_for(sess.start(), timeout=40)
    print("started. sending prompt...")
    await asyncio.wait_for(sess.send(
        "Call the MCP tool aegis_claim with paths='src/foo.py'. Then stop."), timeout=60)
    print("send returned. draining events...")
    kinds = []
    async for ev in sess.events():
        kinds.append(type(ev).__name__)
    print("EVENT KINDS:", kinds)
    print("RECEIVED:", received)
    await asyncio.wait_for(sess.close(), timeout=15)
    print("closed OK")


if __name__ == "__main__":
    asyncio.run(main())
