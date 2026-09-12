"""The stage-5a gate: two terminals, one brain, over a real socket.

The stage-4 gate (tests/views/test_multi_view.py) asserted this in-process
against a list. This asserts it against bytes that crossed a file
descriptor, which is the artifact a user actually touches.
"""
import asyncio

from aegis.config import Agent
from aegis.config.roots import AegisRoots
from aegis.core.manager import SessionManager
from aegis.daemon.protocol import FrameDecoder, encode_data, hello
from aegis.daemon.server import UnixSocketServer
from aegis.views.registry import ViewRegistry

from tests.views.conftest import FakeMCP


class _FakeHarness:
    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...

    async def events(self):
        if False:
            yield


def _brain(tmp_path):
    roots = AegisRoots.for_project(tmp_path)
    roster = {"default": Agent(harness="claude-code", model="opus",
                               effort="high", permission="auto")}
    mgr = SessionManager(roster, "default",
                         make_session=lambda p, u, h: _FakeHarness(),
                         mcp=None, roots=roots)
    reg = ViewRegistry(manager=mgr, roots=roots, mcp=FakeMCP(),
                       agents=roster, default_agent="default",
                       make_session=lambda p, u, h: _FakeHarness())
    return reg, mgr, roots


class _Client:
    """What `aegis attach` is, minus the tty."""

    def __init__(self, reader, writer):
        self.r, self.w = reader, writer
        self.dec = FrameDecoder()
        self.screen = bytearray()
        self._task = None

    def start_reading(self):
        self._task = asyncio.create_task(self._read_forever())

    async def _read_forever(self):
        try:
            while True:
                chunk = await self.r.read(65536)
                if not chunk:
                    return
                for kind, payload in self.dec.feed(chunk):
                    if kind == "D":
                        self.screen.extend(payload)
        except (ConnectionResetError, asyncio.CancelledError):
            return

    async def send(self, data: bytes):
        self.w.write(data)
        await self.w.drain()

    async def close(self):
        if self._task is not None:
            self._task.cancel()
        self.w.close()
        try:
            await self.w.wait_closed()
        except Exception:
            pass


async def _connect(path, view_id, geometry):
    r, w = await asyncio.open_unix_connection(str(path))
    c = _Client(r, w)
    c.start_reading()
    await c.send(hello(view_id, *geometry))
    return c


async def _until(predicate, timeout=20.0):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.02)


async def _quiesce(client, *, rounds=40):
    """Wait until the client's screen stops growing, so later bytes are
    attributable to what the test does next rather than to a boot render
    still draining."""
    stable = 0
    for _ in range(rounds):
        before = len(client.screen)
        await asyncio.sleep(0.05)
        stable = stable + 1 if len(client.screen) == before else 0
        if stable >= 3:
            return
    raise AssertionError("the view never stopped rendering")


def _rows_addressed(screen: bytes) -> set[int]:
    """Which terminal rows these bytes move the cursor to.

    A full frame addresses the height of the view; a status-bar tick
    addresses one row. That difference is the only thing that tells a
    repaint apart from ordinary traffic, which is why this exists rather
    than a byte count.
    """
    import re
    return {int(m.group(1))
            for m in re.finditer(rb"\x1b\[(\d+);\d+H", screen)}


async def test_two_terminals_two_geometries_one_brain(tmp_path):
    reg, mgr, roots = _brain(tmp_path)
    server = UnixSocketServer(roots.state_dir / "daemon.sock", reg)
    await server.start()
    a = b = None
    try:
        a = await _connect(server.path, "term-a", (80, 24))
        b = await _connect(server.path, "term-b", (120, 40))
        await _until(lambda: reg.get("term-a") and reg.get("term-b"))

        await _until(lambda: len(a.screen) > 500)
        await _until(lambda: len(b.screen) > 500)
        assert a.screen, "terminal A got no screen"
        assert b.screen, "terminal B got no screen"

        assert tuple(reg.get("term-a").app.size) == (80, 24)
        assert tuple(reg.get("term-b").app.size) == (120, 40)
    finally:
        for c in (a, b):
            if c is not None:
                await c.close()
        await server.stop()


async def test_a_session_spawned_in_the_brain_reaches_both_terminals(tmp_path):
    """The property session-propagation closed, now over the wire."""
    from aegis.tui.pane import ConversationPane

    reg, mgr, roots = _brain(tmp_path)
    server = UnixSocketServer(roots.state_dir / "daemon.sock", reg)
    await server.start()
    a = b = None
    try:
        a = await _connect(server.path, "term-a", (80, 24))
        b = await _connect(server.path, "term-b", (80, 24))
        await _until(lambda: reg.get("term-a") and reg.get("term-b"))

        await mgr.spawn("default")

        def panes(view_id):
            app = reg.get(view_id).app
            return [p.handle for p in app._panes
                    if isinstance(p, ConversationPane)]

        await _until(lambda: panes("term-a") and panes("term-b"))
        assert panes("term-a") == panes("term-b")
    finally:
        for c in (a, b):
            if c is not None:
                await c.close()
        await server.stop()


async def test_the_socket_is_owner_only(tmp_path):
    """On this transport the mode bits ARE the auth, and the daemon runs
    permission: full."""
    reg, _, roots = _brain(tmp_path)
    server = UnixSocketServer(roots.state_dir / "daemon.sock", reg)
    await server.start()
    try:
        assert server.path.stat().st_mode & 0o777 == 0o600
    finally:
        await server.stop()


async def test_reattach_reuses_the_view_id_and_sends_a_whole_screen(tmp_path):
    """The spec's persistence property: disconnect, reconnect, the view id
    is reused, the focused tab survives, and a full frame arrives.

    The full frame arrives from the fresh view's own boot render, NOT from
    a repaint — a disconnect closes the view in this stage, so there is no
    warm screen to send deltas against. The second half of this test pins
    that a repaint() does reach a connected client, which is the mechanism
    5b needs when a browser reconnects to a view the daemon kept warm.
    """
    reg, _, roots = _brain(tmp_path)
    server = UnixSocketServer(roots.state_dir / "daemon.sock", reg)
    await server.start()
    a = b = None
    try:
        a = await _connect(server.path, "term-a", (80, 24))
        await _until(lambda: reg.get("term-a") is not None)
        await _until(lambda: len(a.screen) > 500)
        reg.get("term-a").state.active_handle = "pinned"
        await a.close()
        await _until(lambda: reg.get("term-a") is None)

        b = await _connect(server.path, "term-a", (80, 24))
        await _until(lambda: reg.get("term-a") is not None)
        assert reg.get("term-a").state.active_handle == "pinned"

        # A full frame is one that ADDRESSES THE WHOLE SCREEN, and that is
        # what has to be asserted. A byte-count threshold is green without
        # any repaint at all: the app keeps emitting small frames on its
        # own timers, so `len(screen) > 500` is satisfied by ordinary
        # traffic. Measured with repaint() stubbed out to `pass`, the
        # byte-count form passed -- the same trap stage 4's mutation 3 fell
        # into (view-seam plan, execution note 6).
        await _until(lambda: len(b.screen) > 500)
        await _quiesce(b)
        b.screen.clear()
        reg.get("term-a").repaint()
        await asyncio.sleep(0.3)
        rows = _rows_addressed(bytes(b.screen))
        assert len(rows) >= 10, (
            f"repaint addressed only {sorted(rows)} -- a full frame covers "
            f"the height of the view, incidental traffic covers a line or "
            f"two")
    finally:
        for c in (a, b):
            if c is not None:
                await c.close()
        await server.stop()


async def test_a_keystroke_from_the_socket_reaches_the_app(tmp_path):
    """The whole inbound path: socket -> _pump -> driver.feed -> app."""
    reg, _, roots = _brain(tmp_path)
    server = UnixSocketServer(roots.state_dir / "daemon.sock", reg)
    await server.start()
    a = None
    try:
        a = await _connect(server.path, "term-a", (80, 24))
        await _until(lambda: reg.get("term-a") is not None
                     and reg.get("term-a").app._driver is not None)
        seen = []
        reg.get("term-a").app._driver.process_message = \
            lambda ev: seen.append(ev)
        await a.send(encode_data(b"z"))
        await _until(lambda: seen)
        assert [getattr(e, "key", None) for e in seen] == ["z"]
    finally:
        if a is not None:
            await a.close()
        await server.stop()


async def test_stopping_the_server_removes_the_socket_file(tmp_path):
    reg, _, roots = _brain(tmp_path)
    server = UnixSocketServer(roots.state_dir / "daemon.sock", reg)
    await server.start()
    assert server.path.exists()
    await server.stop()
    assert not server.path.exists()


async def test_a_stale_socket_file_does_not_block_a_restart(tmp_path):
    """A SIGKILLed daemon leaves its socket file behind."""
    reg, _, roots = _brain(tmp_path)
    path = roots.state_dir / "daemon.sock"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    server = UnixSocketServer(path, reg)
    await server.start()
    a = None
    try:
        a = await _connect(path, "term-a", (80, 24))
        await _until(lambda: reg.get("term-a") is not None)
    finally:
        if a is not None:
            await a.close()
        await server.stop()
