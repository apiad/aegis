"""Against a real brain, real views and a real socket.

Two properties: the relay changes no byte of what the daemon sends, and a
browser survives the daemon going away and coming back.
"""

from __future__ import annotations

import asyncio

from aegis.config import Agent
from aegis.config.roots import AegisRoots
from aegis.daemon.lifecycle import socket_path
from aegis.daemon.protocol import hello
from aegis.daemon.server import UnixSocketServer
from aegis.views.registry import ViewRegistry
from aegis.webterm.relay import ATTACHED, RECONNECTING, relay

from tests.brain import make_brain
from tests.views.conftest import FakeMCP
from tests.webterm.fakes import FakeBrowser, until

READY = b"type a message"


class _FakeHarness:
    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...

    async def events(self):
        if False:
            yield


async def _daemon(roots):
    roster = {
        "main": Agent(
            harness="claude-code", model="opus", effort="high", permission="auto"
        )
    }
    mgr = make_brain(
        roster,
        "main",
        make_session=lambda *a, **k: _FakeHarness(),
        mcp=None,
        roots=roots,
    )
    reg = ViewRegistry(
        manager=mgr,
        roots=roots,
        mcp=FakeMCP(),
        agents=roster,
        default_agent="main",
        make_session=lambda *a, **k: _FakeHarness(),
    )
    server = UnixSocketServer(socket_path(roots), reg)
    await server.start()
    return reg, server


class _Tee:
    def __init__(self, reader):
        self._reader, self.seen = reader, bytearray()

    async def read(self, n):
        data = await self._reader.read(n)
        self.seen.extend(data)
        return data


async def test_the_relay_changes_no_byte(tmp_path):
    roots = AegisRoots.for_project(tmp_path)
    reg, server = await _daemon(roots)
    tees = []

    async def connect():
        r, w = await asyncio.open_unix_connection(str(server.path))
        tees.append(_Tee(r))
        return tees[-1], w

    b = FakeBrowser()
    task = asyncio.create_task(relay(b, connect))
    try:
        b.push(hello("web-eq", 100, 30))
        await until(lambda: READY in b.screen, 30)
        await asyncio.sleep(0.5)
        assert b.screen == bytes(tees[0].seen), (
            "the browser received something other than what the daemon sent"
        )
    finally:
        b.leave()
        await asyncio.wait_for(task, 10)
        await reg.close_all()
        await server.stop()


async def test_a_browser_survives_the_daemon_restarting(tmp_path):
    roots = AegisRoots.for_project(tmp_path)
    reg, server = await _daemon(roots)

    async def connect():
        return await asyncio.open_unix_connection(str(socket_path(roots)))

    b = FakeBrowser()
    task = asyncio.create_task(relay(b, connect, delays=(0.05, 0.1, 0.2)))
    try:
        b.push(hello("web-restart", 100, 30))
        await until(lambda: READY in b.screen, 30)

        await reg.close_all()  # the daemon's views stop and persist
        await server.stop()
        await until(lambda: RECONNECTING in b.texts, 10)

        reg, server = await _daemon(roots)
        await until(lambda: ATTACHED in b.texts, 30)
        at = b.events.index(("text", ATTACHED))
        await until(
            lambda: READY in b"".join(p for k, p in b.events[at:] if k == "bytes"), 30
        )
        after = b"".join(p for k, p in b.events[at:] if k == "bytes")
        assert b"\x1b[?1049h" in after, "the returning view did not draw from scratch"
        assert reg.get("web-restart") is not None, "the view came back under another id"
        assert not task.done(), "the browser was dropped"
    finally:
        b.leave()
        await asyncio.wait_for(task, 10)
        await reg.close_all()
        await server.stop()
