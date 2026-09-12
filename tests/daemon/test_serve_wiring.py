"""`aegis serve` with views=True is the daemon.

Drives the real _serve coroutine against a temp root and connects a real
client to the socket it publishes — not a hand-built ViewRegistry, which is
what every other test in tests/daemon does and which would pass while
_serve's own wiring was missing.
"""
import asyncio

import pytest

from aegis.cli import _serve
from aegis.config import Agent
from aegis.config.roots import AegisRoots
from aegis.daemon import lifecycle
from aegis.daemon import registry as dreg
from aegis.daemon.protocol import FrameDecoder, hello

from tests.views.conftest import FakeMCP


class _FakeHarness:
    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...

    async def events(self):
        if False:
            yield


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_DAEMON_DIR", str(tmp_path / "daemons"))
    monkeypatch.setenv("AEGIS_IDLE_TIMEOUT", "0")


def _roster():
    return {"default": Agent(harness="claude-code", model="opus",
                             effort="high", permission="auto")}


async def _boot(tmp_path, stop, *, views=True):
    roots = AegisRoots.for_project(tmp_path)
    task = asyncio.create_task(_serve(
        roots=roots, agents=_roster(), default_agent="default",
        # **kw, not the (p, u, h) the other test files use: SessionManager
        # adds token= whenever an MCP is bound (manager.py:252), and this
        # is the only test here whose manager has one.
        make_session=lambda agent, url, handle, **kw: _FakeHarness(),
        mcp=FakeMCP(),
        stop=stop, views=views))
    return roots, task


async def _until(predicate, timeout=25.0):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.02)


async def test_serve_publishes_a_socket_a_client_can_attach_to(tmp_path):
    stop = asyncio.Event()
    roots, task = await _boot(tmp_path, stop)
    sock = lifecycle.socket_path(roots)
    try:
        await _until(sock.exists)
        r, w = await asyncio.open_unix_connection(str(sock))
        w.write(hello("term-a", 80, 24))
        await w.drain()
        dec = FrameDecoder()
        screen = bytearray()
        async with asyncio.timeout(25):
            while len(screen) < 500:
                chunk = await r.read(65536)
                if not chunk:
                    break
                for kind, payload in dec.feed(chunk):
                    if kind == "D":
                        screen.extend(payload)
        assert screen, "attached to serve's socket and got no screen"
        w.close()
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=60)


async def test_serve_records_itself_in_the_daemon_registry(tmp_path):
    stop = asyncio.Event()
    roots, task = await _boot(tmp_path, stop)
    try:
        await _until(lambda: dreg.daemon_for(tmp_path) is not None)
        rec = dreg.daemon_for(tmp_path)
        assert rec.socket == lifecycle.socket_path(roots)
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=60)


async def test_serve_forgets_itself_and_removes_the_socket_on_exit(tmp_path):
    stop = asyncio.Event()
    roots, task = await _boot(tmp_path, stop)
    await _until(lambda: dreg.daemon_for(tmp_path) is not None)
    stop.set()
    await asyncio.wait_for(task, timeout=60)
    assert dreg.daemon_for(tmp_path) is None
    assert not lifecycle.socket_path(roots).exists()


async def test_serve_without_views_publishes_no_socket(tmp_path):
    """views=False is today's headless serve, unchanged."""
    stop = asyncio.Event()
    roots, task = await _boot(tmp_path, stop, views=False)
    try:
        await asyncio.sleep(0.5)
        assert not lifecycle.socket_path(roots).exists()
        assert dreg.daemon_for(tmp_path) is None
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=60)
