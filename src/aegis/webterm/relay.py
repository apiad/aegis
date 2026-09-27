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
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

from aegis.daemon.protocol import FrameDecoder, ProtocolError, hello, parse_hello

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


RECONNECTING = json.dumps({"type": "reconnecting"})
ATTACHED = json.dumps({"type": "attached"})
_DELAYS = (0.25, 0.5, 1.0, 2.0, 5.0)


async def relay(
    browser: BrowserSocket, connect: Connect, *, delays: tuple[float, ...] = _DELAYS
) -> None:
    """Hold one browser for its whole life, across daemon restarts.

    The browser's socket stays open while the daemon is gone. On reconnect
    the daemon is asked for the same view id at the browser's current size;
    `serve_view` persisted that view when the old connection closed, so the
    new one restores it and boots with a full frame.
    """
    first = await browser.receive()
    # isinstance first, so the bytes() below is narrowed: _hello_of already
    # rejects anything that is not bytes, so the guard is the same guard.
    if not isinstance(first, (bytes, bytearray)):
        return
    greeting = _hello_of(first)
    if greeting is None:
        return
    view_id, width, height, _open = greeting
    size = [width, height]
    inbox: asyncio.Queue = asyncio.Queue()
    reader_task = asyncio.create_task(_read_browser(browser, inbox))
    opening: bytes | None = bytes(first)
    failures = 0
    try:
        while True:
            if opening is None and failures:
                delay = delays[min(failures - 1, len(delays) - 1)]
                if await _browser_left_within(delay, reader_task):
                    return
            try:
                reader, writer = await connect()
            except Exception as e:  # noqa: BLE001 — the daemon may be down
                log.info("daemon unreachable (%s); retrying", e)
                failures += 1
                opening = None
                continue
            try:
                if opening is None:
                    # After connect(), not before: connect() is where a
                    # restart spends its gap (ensure_daemon spawning a
                    # daemon), so keys typed at any point in it are dropped.
                    if not _drop_stale_input(inbox, size):
                        return
                writer.write(
                    opening if opening is not None else hello(view_id, size[0], size[1])
                )
                await writer.drain()
                announce = opening is None
                opening = None
                outcome, got_bytes = await _pipe(
                    reader, writer, inbox, browser, size, announce
                )
            finally:
                with contextlib.suppress(Exception):
                    writer.close()
                    await writer.wait_closed()
            if outcome == "browser":
                return
            failures = 0 if got_bytes else failures + 1
            with contextlib.suppress(Exception):
                await browser.send_text(RECONNECTING)
    finally:
        reader_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reader_task


async def _read_browser(browser: BrowserSocket, inbox: asyncio.Queue) -> None:
    try:
        while True:
            message = await browser.receive()
            inbox.put_nowait(message)
            if message is None:
                return
    except Exception:  # noqa: BLE001 — a broken socket is a browser gone
        inbox.put_nowait(None)


def _note_resize(message: bytes, size: list[int]) -> None:
    for kind, payload in FrameDecoder().feed(message):
        if kind != "M":
            continue
        with contextlib.suppress(ValueError, TypeError):
            obj = json.loads(payload)
            if obj.get("type") == "resize":
                size[0], size[1] = int(obj["width"]), int(obj["height"])


def _drop_stale_input(inbox: asyncio.Queue, size: list[int]) -> bool:
    """Discard keys typed while no view was listening; keep their resizes.
    False when the browser left in the meantime."""
    while not inbox.empty():
        message = inbox.get_nowait()
        if message is None:
            return False
        if isinstance(message, (bytes, bytearray)):
            _note_resize(bytes(message), size)
    return True


async def _browser_left_within(delay: float, reader_task: asyncio.Task) -> bool:
    done, _ = await asyncio.wait({reader_task}, timeout=delay)
    return bool(done)


async def _pipe(
    reader,
    writer,
    inbox: asyncio.Queue,
    browser: BrowserSocket,
    size: list[int],
    announce: bool,
) -> tuple[str, bool]:
    """Pump until one side ends. Returns who ended it and whether the daemon
    sent anything, which separates a view from a refusal."""
    got_bytes = False

    async def up() -> str:
        while True:
            message = await inbox.get()
            if message is None:
                return "browser"
            if isinstance(message, str):
                continue
            _note_resize(bytes(message), size)
            try:
                writer.write(bytes(message))
                await writer.drain()
            except (ConnectionError, OSError):
                return "daemon"

    async def down() -> str:
        nonlocal got_bytes
        while True:
            try:
                chunk = await reader.read(65536)
            except (ConnectionError, OSError):
                return "daemon"
            if not chunk:
                return "daemon"
            try:
                if not got_bytes and announce:
                    await browser.send_text(ATTACHED)
                got_bytes = True
                await browser.send_bytes(chunk)
            except Exception:  # noqa: BLE001
                return "browser"

    tasks = {asyncio.create_task(up()), asyncio.create_task(down())}
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        return next(iter(done)).result(), got_bytes
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
