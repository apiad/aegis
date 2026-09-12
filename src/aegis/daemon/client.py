"""`aegis attach`: a pipe with a window size.

It holds no aegis state and parses no aegis concepts — not a session, not
an agent, not a queue. That constraint is the entire difference between
this and the `--remote` client it replaces, whose protocol grew a message
for every feature until it fell behind the TUI. The only aegis import here
is the frame codec, and it should stay the only one.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys
import termios
import tty
from pathlib import Path
from typing import BinaryIO

from aegis.daemon.protocol import FrameDecoder, encode_data, hello, resize


def terminal_size(fd: int) -> tuple[int, int]:
    """(columns, lines) for ``fd``, or Textual's own 80x24 when it is not a
    terminal — a pipe, or systemd's /dev/null."""
    try:
        size = os.get_terminal_size(fd)
    except OSError:
        return (80, 24)
    return (size.columns or 80, size.lines or 24)


@contextlib.contextmanager
def _raw(fd: int):
    """Raw mode, restored on every exit path including a traceback.

    A client that dies without restoring leaves the user's shell with no
    echo and no line discipline, which reads as a hung terminal rather
    than as a crash.
    """
    try:
        saved = termios.tcgetattr(fd)
    except termios.error:
        yield          # not a tty: nothing to set, nothing to restore
        return
    try:
        tty.setraw(fd)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


async def attach(path: str | Path, view_id: str, *, stdin_fd: int = 0,
                 stdout: BinaryIO | None = None) -> None:
    """Connect to a daemon's unix socket and pipe until either end stops."""
    out = stdout if stdout is not None else sys.stdout.buffer
    reader, writer = await asyncio.open_unix_connection(str(path))
    loop = asyncio.get_running_loop()

    width, height = terminal_size(stdin_fd)
    writer.write(hello(view_id, width, height))
    await writer.drain()

    def _on_winch() -> None:
        w, h = terminal_size(stdin_fd)
        writer.write(resize(w, h))

    with contextlib.suppress(NotImplementedError, ValueError, OSError):
        loop.add_signal_handler(signal.SIGWINCH, _on_winch)

    stdin_q: asyncio.Queue = asyncio.Queue()

    def _readable() -> None:
        try:
            data = os.read(stdin_fd, 65536)
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            data = b""
        stdin_q.put_nowait(data)

    async def _pump_in() -> None:
        while True:
            data = await stdin_q.get()
            if not data:
                return
            writer.write(encode_data(data))
            await writer.drain()

    async def _pump_out() -> None:
        decoder = FrameDecoder()
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                return
            for kind, payload in decoder.feed(chunk):
                if kind == "D":
                    out.write(payload)
                    out.flush()
                # Meta from the daemon is Textual's {"type": "exit"} and
                # its delivery packets. Nothing here needs to act on them:
                # the daemon closes the socket when the view ends, and EOF
                # is what this loop already terminates on. Acting on meta
                # would be the client learning aegis concepts.

    with _raw(stdin_fd):
        loop.add_reader(stdin_fd, _readable)
        tasks = [asyncio.create_task(_pump_in()),
                 asyncio.create_task(_pump_out())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks:
                t.cancel()
            for t in tasks:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await t
            with contextlib.suppress(Exception):
                loop.remove_reader(stdin_fd)
            with contextlib.suppress(NotImplementedError, ValueError,
                                     OSError):
                loop.remove_signal_handler(signal.SIGWINCH)
            with contextlib.suppress(Exception):
                writer.close()
                await writer.wait_closed()
