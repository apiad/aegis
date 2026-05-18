from __future__ import annotations

import asyncio
import contextlib
import socket

from aegis.mcp.bridge import AppBridge, SessionInfo
from aegis.mcp.server import build_server


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _NullBridge:
    """Defensive fallback if AegisMCP.start() runs without bind().
    Tools return empty/unavailable rather than crashing."""

    def list_sessions(self) -> list[SessionInfo]:
        return []

    def list_agents(self) -> list[str]:
        return []

    async def handoff(self, from_handle: str, target_handle: str,
                      context: str) -> str:
        return "aegis bridge unavailable"


class AegisMCP:
    """The shared aegis MCP plane: one FastMCP server over HTTP,
    co-resident in the app's asyncio loop."""

    def __init__(self) -> None:
        self._bridge: AppBridge | None = None
        self._server = None
        self.host = "127.0.0.1"
        self.port = _free_port()
        self._task: asyncio.Task | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/mcp/"

    def bind(self, bridge: AppBridge) -> None:
        self._bridge = bridge

    async def start(self) -> None:
        if self._task is not None:
            return
        bridge = self._bridge if self._bridge is not None else _NullBridge()
        self._server = build_server(bridge)
        self._task = asyncio.create_task(
            self._server.run_http_async(
                host=self.host, port=self.port, show_banner=False))
        # wait until the port accepts (server ready) or time out
        for _ in range(100):  # ~5s max
            try:
                with socket.create_connection(
                        (self.host, self.port), timeout=0.2):
                    return
            except OSError:
                await asyncio.sleep(0.05)
        await self.stop()
        raise RuntimeError(
            f"aegis MCP server did not start on {self.host}:{self.port}")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self._task
        self._task = None
