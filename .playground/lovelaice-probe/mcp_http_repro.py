"""Isolate the HTTP ManagedMcpSession path against a live FastMCP server."""
import asyncio
import socket
import threading
import time


def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def _serve(srv, port):
    import asyncio as _a
    loop = _a.new_event_loop(); _a.set_event_loop(loop)
    loop.run_until_complete(srv.run_http_async(host="127.0.0.1", port=port, show_banner=False))


async def main():
    from fastmcp import FastMCP
    from lovelaice.mcp import build_agent_tools

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

    try:
        spec = {"name": "aegis", "url": f"http://127.0.0.1:{port}/mcp", "headers": {}}
        # build_agent_tools is sync (spawns its own thread); run off-loop.
        tools, sessions = await asyncio.to_thread(build_agent_tools, [spec])
        print("TOOLS:", [t.name for t in tools])
        for t in tools:
            if t.name.endswith("aegis_claim"):
                out = await t.inner.run(paths="src/foo.py")
                print("CALL OUT:", out)
        print("RECEIVED:", received)
        for s in sessions:
            await s.aclose()
    except Exception:
        import traceback; traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
