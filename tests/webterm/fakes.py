"""A browser and a daemon for relay tests.

The daemon is a real unix socket: the relay's reads and writes cross a file
descriptor, and a socket splits and closes the way the real one does. The
browser is a queue, because the WebSocket layer has its own tests.
"""
from __future__ import annotations

import asyncio
from pathlib import Path


class FakeBrowser:
    def __init__(self) -> None:
        self._inbox: asyncio.Queue = asyncio.Queue()
        self.events: list[tuple[str, bytes | str]] = []   # in arrival order

    async def receive(self):
        return await self._inbox.get()

    async def send_bytes(self, data: bytes) -> None:
        self.events.append(("bytes", bytes(data)))

    async def send_text(self, text: str) -> None:
        self.events.append(("text", text))

    def push(self, message) -> None:
        self._inbox.put_nowait(message)

    def leave(self) -> None:
        self._inbox.put_nowait(None)

    @property
    def screen(self) -> bytes:
        return b"".join(p for k, p in self.events if k == "bytes")

    @property
    def texts(self) -> list[str]:
        return [p for k, p in self.events if k == "text"]


class FakeDaemon:
    """Records every connection's bytes; can speak and hang up."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.received: list[bytearray] = []
        self.writers: list[asyncio.StreamWriter] = []
        self.eof: list[asyncio.Event] = []
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_unix_server(self._on, path=str(self.path))

    async def _on(self, reader, writer) -> None:
        i = len(self.received)
        self.received.append(bytearray())
        self.writers.append(writer)
        self.eof.append(asyncio.Event())
        while chunk := await reader.read(65536):
            self.received[i].extend(chunk)
        self.eof[i].set()

    async def say(self, i: int, data: bytes) -> None:
        self.writers[i].write(data)
        await self.writers[i].drain()

    async def hang_up(self, i: int) -> None:
        self.writers[i].close()

    async def connect(self):
        return await asyncio.open_unix_connection(str(self.path))

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            for w in self.writers:
                w.close()


async def until(predicate, timeout: float = 5.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)
