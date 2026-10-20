"""The websocket's deflate window: the standard 32 KB, not uvicorn's 4 KB (#525)."""

import asyncio
import socket

import uvicorn
from starlette.applications import Starlette
from starlette.routing import WebSocketRoute

from aegis.web import ws_protocol


async def _echo(ws):
    await ws.accept()
    await ws.close()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _handshake(port: int) -> str:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(
        (
            f"GET /ws HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nUpgrade: websocket\r\n"
            "Connection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            # What Chrome offers.
            "Sec-WebSocket-Extensions: permessage-deflate; client_max_window_bits\r\n\r\n"
        ).encode()
    )
    head = (await reader.readuntil(b"\r\n\r\n")).decode()
    writer.close()
    return next(
        line.split(":", 1)[1].strip()
        for line in head.split("\r\n")
        if line.lower().startswith("sec-websocket-extensions")
    )


def test_the_socket_negotiates_deflate_with_the_full_window():
    async def main():
        port = _free_port()
        app = Starlette(routes=[WebSocketRoute("/ws", _echo)])
        config = uvicorn.Config(app, port=port, log_level="warning", ws=ws_protocol())
        server = uvicorn.Server(config)
        task = asyncio.create_task(server.serve())
        while not server.started:
            await asyncio.sleep(0.01)
        try:
            return await _handshake(port)
        finally:
            server.should_exit = True
            await task

    assert asyncio.run(main()) == "permessage-deflate"
