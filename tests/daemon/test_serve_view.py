"""One attached client, over a pair of in-memory pipes.

No socket here on purpose: everything this file asserts is about the
connection's *logic*, and a test mediated by a socket fails for two
reasons. The real socket gets its own test in test_unix_socket.py.
"""
import asyncio

from aegis.config import Agent
from aegis.config.roots import AegisRoots
from aegis.core.manager import SessionManager
from aegis.daemon.protocol import FrameDecoder, encode_data, hello
from aegis.daemon.server import serve_view
from aegis.views.registry import ViewRegistry

from tests.views.conftest import FakeMCP


class _FakeHarness:
    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...

    async def events(self):
        if False:
            yield


class _Pipe:
    """A reader the test pushes into and a writer it reads out of."""

    def __init__(self):
        self._q: asyncio.Queue = asyncio.Queue()
        self.sent = bytearray()
        self.closed = False

    # reader side
    async def read(self, n: int = -1) -> bytes:
        return await self._q.get()

    def push(self, data: bytes) -> None:
        self._q.put_nowait(data)

    def eof(self) -> None:
        self._q.put_nowait(b"")

    # writer side
    def write(self, data: bytes) -> None:
        self.sent.extend(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


def _reg(tmp_path):
    roots = AegisRoots.for_project(tmp_path)
    roster = {"default": Agent(harness="claude-code", model="opus",
                               effort="high", permission="auto")}
    mgr = SessionManager(roster, "default",
                         make_session=lambda p, u, h: _FakeHarness(),
                         mcp=None, roots=roots)
    return ViewRegistry(manager=mgr, roots=roots, mcp=FakeMCP()), mgr


async def _until(predicate, timeout=10.0):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.02)


async def test_frames_reach_the_client(tmp_path):
    reg, _ = _reg(tmp_path)
    pipe = _Pipe()
    pipe.push(hello("v1", 80, 24))
    task = asyncio.create_task(serve_view(pipe, pipe, reg))
    try:
        await _until(lambda: b"\x1b[?1049h" in bytes(pipe.sent))
    finally:
        pipe.eof()
        await asyncio.wait_for(task, timeout=15)


async def test_the_client_gets_whole_frames(tmp_path):
    """Every byte the client receives must decode as a frame. A transport
    that wrote a partial or a doubled header would still show a plausible
    screen in a terminal and be unusable to a browser."""
    reg, _ = _reg(tmp_path)
    pipe = _Pipe()
    pipe.push(hello("v1", 80, 24))
    task = asyncio.create_task(serve_view(pipe, pipe, reg))
    try:
        await _until(lambda: len(pipe.sent) > 1000)
    finally:
        pipe.eof()
        await asyncio.wait_for(task, timeout=15)
    dec = FrameDecoder()
    frames = list(dec.feed(bytes(pipe.sent)))
    assert frames, "nothing decoded"
    assert all(k in ("D", "M") for k, _ in frames)
    assert len(dec._buf) < 5, "a partial frame was left on the wire"


async def test_the_hello_geometry_is_the_views_geometry(tmp_path):
    reg, _ = _reg(tmp_path)
    pipe = _Pipe()
    pipe.push(hello("v1", 120, 40))
    task = asyncio.create_task(serve_view(pipe, pipe, reg))
    try:
        await _until(lambda: reg.get("v1") is not None)
        await _until(lambda: reg.get("v1").state.geometry == (120, 40))
    finally:
        pipe.eof()
        await asyncio.wait_for(task, timeout=15)


async def test_client_bytes_reach_the_views_driver(tmp_path):
    reg, _ = _reg(tmp_path)
    pipe = _Pipe()
    pipe.push(hello("v1", 80, 24))
    task = asyncio.create_task(serve_view(pipe, pipe, reg))
    try:
        await _until(lambda: reg.get("v1") is not None
                     and reg.get("v1").app._driver is not None)
        seen = []
        reg.get("v1").app._driver.process_message = lambda ev: seen.append(ev)
        pipe.push(encode_data(b"q"))
        await _until(lambda: seen)
        assert [getattr(e, "key", None) for e in seen] == ["q"]
    finally:
        pipe.eof()
        await asyncio.wait_for(task, timeout=15)


async def test_eof_closes_the_view(tmp_path):
    reg, _ = _reg(tmp_path)
    pipe = _Pipe()
    pipe.push(hello("v1", 80, 24))
    task = asyncio.create_task(serve_view(pipe, pipe, reg))
    await _until(lambda: reg.get("v1") is not None)
    pipe.eof()
    await asyncio.wait_for(task, timeout=15)
    assert reg.get("v1") is None, "the view outlived its only client"


async def test_the_view_state_survives_the_disconnect(tmp_path):
    """Reattach restores what this terminal was looking at."""
    reg, _ = _reg(tmp_path)
    pipe = _Pipe()
    pipe.push(hello("v1", 80, 24))
    task = asyncio.create_task(serve_view(pipe, pipe, reg))
    await _until(lambda: reg.get("v1") is not None)
    reg.get("v1").state.active_handle = "some-handle"
    pipe.eof()
    await asyncio.wait_for(task, timeout=15)

    from aegis.views.state import load_view
    restored = load_view(reg._roots.state_dir, "v1")
    assert restored is not None and restored.active_handle == "some-handle"


async def test_a_malformed_hello_closes_before_a_view_is_built(tmp_path):
    """Assert on the substrate — no view in the registry — not on an error
    frame coming back. A daemon that built the view and *then* rejected the
    client has already run the expensive, stateful half."""
    reg, _ = _reg(tmp_path)
    pipe = _Pipe()
    pipe.push(b"M" + (4).to_bytes(4, "big") + b"nope")
    await asyncio.wait_for(serve_view(pipe, pipe, reg), timeout=15)
    assert reg.list() == []
    assert pipe.closed


async def test_a_second_client_for_a_live_view_is_refused(tmp_path):
    """One client per view id. ViewRegistry.open returns the EXISTING view
    for a live id, so without this guard two sockets would both attach to
    one app, and the second's repaint would clear the first's screen."""
    reg, _ = _reg(tmp_path)
    a, b = _Pipe(), _Pipe()
    a.push(hello("v1", 80, 24))
    ta = asyncio.create_task(serve_view(a, a, reg))
    await _until(lambda: reg.get("v1") is not None)
    b.push(hello("v1", 80, 24))
    await asyncio.wait_for(serve_view(b, b, reg), timeout=15)
    assert b.closed
    assert reg.get("v1") is not None, \
        "the refusal took the first client's view"
    a.eof()
    await asyncio.wait_for(ta, timeout=15)
