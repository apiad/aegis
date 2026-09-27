"""One browser tab to one daemon connection.

Frames cross unchanged in both directions. The daemon's `serve_view` sees a
client indistinguishable from `aegis attach`, which is the property the
relay-equivalence gate asserts.

The relay parses exactly one thing, the first frame, because a client that
has not said hello must not reach the daemon at all.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

from aegis.daemon.protocol import FrameDecoder, ProtocolError, parse_hello

log = logging.getLogger(__name__)


class BrowserSocket(Protocol):
    async def receive(self) -> bytes | str | None: ...
    async def send_bytes(self, data: bytes) -> None: ...
    async def send_text(self, text: str) -> None: ...


Connect = Callable[[], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]]


def _hello_of(message) -> tuple[str, int, int, str | None] | None:
    if not isinstance(message, (bytes, bytearray)):
        return None
    frames = list(FrameDecoder().feed(bytes(message)))
    if not frames or frames[0][0] != "M":
        return None
    try:
        return parse_hello(frames[0][1])
    except ProtocolError:
        return None


async def relay(browser: BrowserSocket, connect: Connect) -> None:
    first = await browser.receive()
    # isinstance first, so the bytes() below is narrowed: _hello_of already
    # rejects anything that is not bytes, so the guard is the same guard.
    if not isinstance(first, (bytes, bytearray)) or _hello_of(first) is None:
        return
    reader, writer = await connect()
    try:
        writer.write(bytes(first))
        await writer.drain()
        up = asyncio.create_task(_up(browser, writer))
        down = asyncio.create_task(_down(reader, browser))
        try:
            await asyncio.wait({up, down}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in (up, down):
                t.cancel()
            for t in (up, down):
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await t
    finally:
        with contextlib.suppress(Exception):
            writer.close()
            await writer.wait_closed()


async def _up(browser: BrowserSocket, writer: asyncio.StreamWriter) -> None:
    while True:
        message = await browser.receive()
        if message is None:
            return
        if isinstance(message, str):
            continue  # the page sends no text today
        writer.write(bytes(message))
        await writer.drain()


async def _down(reader: asyncio.StreamReader, browser: BrowserSocket) -> None:
    while chunk := await reader.read(65536):
        await browser.send_bytes(chunk)
