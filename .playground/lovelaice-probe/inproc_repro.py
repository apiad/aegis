"""In-process AcpServerV1 + real model + HTTP MCP — surfaces the real traceback
the subprocess hides behind 'Internal error'."""
import asyncio
import os
import socket
import threading
import time
from pathlib import Path


def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def _serve(srv, port):
    loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
    loop.run_until_complete(srv.run_http_async(host="127.0.0.1", port=port, show_banner=False))


class _Conn:
    async def session_update(self, session_id, update, **kw):
        pass


async def main():
    os.environ["OPENROUTER_API_KEY"] = Path("/home/apiad/Workspace/.claude/openrouter.token").read_text().strip()
    os.environ["LOVELAICE_MODEL"] = "anthropic/claude-haiku-4-5"
    os.environ["LOVELAICE_BASE_URL"] = "https://openrouter.ai/api/v1"

    from fastmcp import FastMCP
    from lovelaice.acp.v1.server import AcpServerV1
    from lovelaice.acp.v1.__main__ import _default_factory

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
    print("server up")

    server = AcpServerV1(agent_factory=_default_factory)
    server.on_connect(_Conn())
    spec = {"name": "aegis", "url": f"http://127.0.0.1:{port}/mcp", "headers": []}
    resp = await server.new_session(cwd="/tmp", mcp_servers=[spec])
    print("session:", resp.session_id, "tools on agent:",
          [t.name for t in server._sessions[resp.session_id].harness.tools.all()])
    r = await server.prompt(
        prompt=[{"type": "text", "text": "Call the tool aegis_claim with paths='src/foo.py'. Then stop."}],
        session_id=resp.session_id)
    print("stop_reason:", r.stop_reason, "received:", received)


if __name__ == "__main__":
    asyncio.run(main())
