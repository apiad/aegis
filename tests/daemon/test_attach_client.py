"""The client is a pipe. These tests hold it to that.

Driven over a socketpair with an os.pipe standing in for stdin, so the
assertions are about bytes rather than about a tty.
"""
import asyncio
import json
import os

from aegis.daemon.client import attach, terminal_size
from aegis.daemon.protocol import FrameDecoder, encode_data


class _Collector:
    def __init__(self):
        self.buf = bytearray()

    def write(self, data):
        self.buf.extend(data)

    def flush(self):
        return None


async def _fake_daemon(path, out: list, ready: asyncio.Event):
    """Accept one client, record what it sends, feed it a screen."""

    async def handle(reader, writer):
        dec = FrameDecoder()
        ready.set()
        writer.write(encode_data(b"SCREEN"))
        await writer.drain()
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            out.extend(dec.feed(chunk))
        writer.close()

    return await asyncio.start_unix_server(handle, path=str(path))


async def _until(predicate, timeout=10.0):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.02)


async def test_the_first_frame_is_a_hello_naming_the_view(tmp_path):
    sock = tmp_path / "d.sock"
    got, ready = [], asyncio.Event()
    server = await _fake_daemon(sock, got, ready)
    r_fd, w_fd = os.pipe()
    out = _Collector()
    task = asyncio.create_task(
        attach(sock, "term-a", stdin_fd=r_fd, stdout=out))
    try:
        await asyncio.wait_for(ready.wait(), timeout=5)
        await _until(lambda: got)
        kind, payload = got[0]
        assert kind == "M"
        obj = json.loads(payload)
        assert obj["type"] == "hello" and obj["view_id"] == "term-a"
        assert isinstance(obj["width"], int) and obj["width"] >= 1
    finally:
        os.close(w_fd)
        await asyncio.wait_for(task, timeout=10)
        server.close()


async def test_stdin_bytes_are_forwarded_as_data_frames(tmp_path):
    sock = tmp_path / "d.sock"
    got, ready = [], asyncio.Event()
    server = await _fake_daemon(sock, got, ready)
    r_fd, w_fd = os.pipe()
    out = _Collector()
    task = asyncio.create_task(
        attach(sock, "term-a", stdin_fd=r_fd, stdout=out))
    try:
        await asyncio.wait_for(ready.wait(), timeout=5)
        os.write(w_fd, b"hello")
        await _until(lambda: ("D", b"hello") in got)
    finally:
        os.close(w_fd)
        await asyncio.wait_for(task, timeout=10)
        server.close()


async def test_data_frames_from_the_daemon_are_unwrapped_to_stdout(tmp_path):
    """The client writes PAYLOADS to the terminal, not frames. Writing the
    frames verbatim also 'shows something', which is why this asserts the
    header is absent rather than that output is non-empty."""
    sock = tmp_path / "d.sock"
    got, ready = [], asyncio.Event()
    server = await _fake_daemon(sock, got, ready)
    r_fd, w_fd = os.pipe()
    out = _Collector()
    task = asyncio.create_task(
        attach(sock, "term-a", stdin_fd=r_fd, stdout=out))
    try:
        await _until(lambda: b"SCREEN" in bytes(out.buf))
        assert bytes(out.buf) == b"SCREEN"
    finally:
        os.close(w_fd)
        await asyncio.wait_for(task, timeout=10)
        server.close()


async def test_the_client_exits_when_the_daemon_closes(tmp_path):
    sock = tmp_path / "d.sock"
    ready = asyncio.Event()

    async def handle(reader, writer):
        ready.set()
        await reader.read(65536)
        writer.close()

    server = await asyncio.start_unix_server(handle, path=str(sock))
    r_fd, w_fd = os.pipe()
    out = _Collector()
    task = asyncio.create_task(
        attach(sock, "term-a", stdin_fd=r_fd, stdout=out))
    await asyncio.wait_for(ready.wait(), timeout=5)
    await asyncio.wait_for(task, timeout=15)
    os.close(w_fd)
    os.close(r_fd)
    server.close()


async def test_sigwinch_sends_a_resize_frame(tmp_path):
    """The one behaviour that makes this more than `cat`.

    SIGWINCH's default disposition is to be ignored, so sending it to our
    own pid is safe; what it proves is that the client registered a
    handler and that the handler puts a resize on the wire.
    """
    sock = tmp_path / "d.sock"
    got, ready = [], asyncio.Event()
    server = await _fake_daemon(sock, got, ready)
    r_fd, w_fd = os.pipe()
    out = _Collector()
    task = asyncio.create_task(
        attach(sock, "term-a", stdin_fd=r_fd, stdout=out))
    try:
        await asyncio.wait_for(ready.wait(), timeout=5)
        await _until(lambda: got)          # the hello landed
        import signal as _signal
        os.kill(os.getpid(), _signal.SIGWINCH)

        def _resized():
            return any(k == "M" and json.loads(p).get("type") == "resize"
                       for k, p in got)

        await _until(_resized)
    finally:
        os.close(w_fd)
        await asyncio.wait_for(task, timeout=10)
        server.close()


def test_terminal_size_falls_back_when_the_fd_is_not_a_tty():
    r_fd, w_fd = os.pipe()
    try:
        assert terminal_size(r_fd) == (80, 24)
    finally:
        os.close(r_fd)
        os.close(w_fd)
