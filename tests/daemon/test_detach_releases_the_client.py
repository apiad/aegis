"""Ctrl+Q must end the connection, not just the app.

`serve_view` starts the view and then awaits `_pump`, which reads the
client socket until EOF. Nothing watches the app. So when a detach exits
the app, the app is gone, the frames stop, and the client is still sitting
in its read loop with a terminal that no longer answers. From the user's
side Ctrl+Q freezes.

The view is left in the registry too, still keyed by that tty, which is
the second half of the damage: the next `aegis` from the same terminal
either gets refused as "already has a client" or is handed a view whose
app is dead.

Both assertions are about what the client can observe. Asserting that the
app exited would have passed throughout: it always did.
"""
import asyncio

import pytest

from aegis.cli import _serve
from aegis.config import Agent
from aegis.config.roots import AegisRoots
from aegis.daemon import lifecycle
from aegis.daemon.protocol import FrameDecoder, encode_data, hello
from aegis.events import AssistantText, Result, SystemInit

from tests.views.conftest import FakeMCP

CTRL_Q = b"\x11"


class _FakeHarness:
    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...

    async def events(self):
        yield SystemInit(session_id="sid-1")
        yield AssistantText("ok", usage=None)
        yield Result(duration_ms=1, is_error=False)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_DAEMON_DIR", str(tmp_path / "daemons"))
    monkeypatch.setenv("AEGIS_IDLE_TIMEOUT", "0")


def _roster():
    return {"opus": Agent(harness="claude-code", model="opus",
                          effort="high", permission="auto")}


async def _serve_task(roots, stop):
    return asyncio.create_task(_serve(
        roots=roots, agents=_roster(), default_agent="opus",
        make_session=lambda agent, url, handle, **kw: _FakeHarness(),
        mcp=FakeMCP(), stop=stop, views=True))


async def _attach(roots, view_id, *, settle=4.0):
    sock = lifecycle.socket_path(roots)
    async with asyncio.timeout(25):
        while not sock.exists():
            await asyncio.sleep(0.02)
    reader, writer = await asyncio.open_unix_connection(str(sock))
    writer.write(hello(view_id, 100, 30))
    await writer.drain()
    decoder = FrameDecoder()
    out = bytearray()
    try:
        async with asyncio.timeout(settle):
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    break
                for kind, payload in decoder.feed(chunk):
                    if kind == "D":
                        out.extend(payload)
    except TimeoutError:
        pass
    return reader, writer, out.decode("utf-8", "replace")


async def test_ctrl_q_closes_the_connection_and_frees_the_view(tmp_path):
    roots = AegisRoots.for_project(tmp_path)
    stop = asyncio.Event()
    task = await _serve_task(roots, stop)
    try:
        reader, writer, _screen = await _attach(roots, "tty-detach")

        writer.write(encode_data(CTRL_Q))
        await writer.drain()

        closed = False
        try:
            async with asyncio.timeout(10):
                while True:
                    if not await reader.read(65536):
                        closed = True
                        break
        except TimeoutError:
            pass

        assert closed, (
            "the app exited but the client was never released; "
            "this is the frozen terminal after Ctrl+Q")
        writer.close()
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=60)


async def test_a_session_survives_a_detach_and_comes_back_on_reattach(tmp_path):
    """The reason the daemon exists. Detach, reattach, find the same
    session, asserted on the tab the second client is actually shown."""
    roots = AegisRoots.for_project(tmp_path)
    stop = asyncio.Event()
    task = await _serve_task(roots, stop)
    try:
        reader, writer, first = await _attach(roots, "tty-round")
        handle = next((w.split()[0] for w in first.split()
                       if "-" in w and w.replace("-", "").isalpha()), None)
        assert handle, f"no tab name in the first screen: {first[-200:]!r}"

        writer.write(encode_data(CTRL_Q))
        await writer.drain()
        try:
            async with asyncio.timeout(10):
                while await reader.read(65536):
                    pass
        except TimeoutError:
            pass
        writer.close()

        _r2, w2, second = await _attach(roots, "tty-round")
        assert handle in second, (
            f"reattach did not bring back {handle}; "
            f"second screen tail: {second[-300:]!r}")
        assert "type a message" in second, "reattached to an unusable screen"
        w2.close()
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=60)


async def test_closing_the_last_tab_also_releases_the_client(tmp_path):
    """Ctrl+W on the last tab exits the app, by the same route Ctrl+Q does,
    and so hung the same way. It read as "Ctrl+W does nothing": the tab
    went, the app went, and the terminal stayed."""
    roots = AegisRoots.for_project(tmp_path)
    stop = asyncio.Event()
    task = await _serve_task(roots, stop)
    try:
        reader, writer, _screen = await _attach(roots, "tty-lasttab")

        writer.write(encode_data(b"\x17"))          # ctrl+w
        await writer.drain()

        closed = False
        try:
            async with asyncio.timeout(10):
                while True:
                    if not await reader.read(65536):
                        closed = True
                        break
        except TimeoutError:
            pass

        assert closed, "closing the last tab left the client hanging"
        writer.close()
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=60)


async def test_the_client_receives_the_terminal_restore_before_the_close(
        tmp_path):
    """The driver writing the escapes is half of it; they have to arrive.

    `serve_view` races the pump against the app and then cancels the
    flusher, so bytes the app wrote on its way down can still be sitting in
    the pending buffer when the socket closes. Dropping them leaves the
    terminal in exactly the state the escapes exist to undo, which is
    indistinguishable from never writing them.

    The flusher polls every 5ms and usually wins that race on its own,
    which is why removing the final drain does not fail this test. The
    test below starves the flusher to pin the drain itself.
    """
    roots = AegisRoots.for_project(tmp_path)
    stop = asyncio.Event()
    task = await _serve_task(roots, stop)
    try:
        reader, writer, _screen = await _attach(roots, "tty-restore")

        writer.write(encode_data(CTRL_Q))
        await writer.drain()

        decoder = FrameDecoder()
        tail = bytearray()
        try:
            async with asyncio.timeout(10):
                while True:
                    chunk = await reader.read(65536)
                    if not chunk:
                        break
                    for kind, payload in decoder.feed(chunk):
                        if kind == "D":
                            tail.extend(payload)
        except TimeoutError:
            pass

        got = bytes(tail)
        for what, seq in [("leave alt screen", b"?1049l"),
                          ("show cursor", b"?25h"),
                          ("mouse off", b"?1003l"),
                          ("bracketed paste off", b"?2004l")]:
            assert seq in got, (
                f"{what} never reached the client; the terminal is left in "
                f"that mode after detach ({len(got)} bytes arrived)")
        writer.close()
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=60)


async def test_the_restore_survives_a_flusher_that_never_runs(
        tmp_path, monkeypatch):
    """Pins the final drain, which the test above cannot.

    Whether the app's last bytes reach the socket is otherwise a race
    between a 5ms poll and the cancellation right after it, and a race the
    poll usually wins is still a race: it is exactly the kind that fails on
    a loaded machine and nowhere else. Starving the flusher makes the drain
    the only route, so removing it fails here every time.
    """
    import aegis.daemon.server as server

    async def _never(_writer, _pending):
        await asyncio.Event().wait()

    monkeypatch.setattr(server, "_flush", _never)

    roots = AegisRoots.for_project(tmp_path)
    stop = asyncio.Event()
    task = await _serve_task(roots, stop)
    try:
        sock = lifecycle.socket_path(roots)
        async with asyncio.timeout(25):
            while not sock.exists():
                await asyncio.sleep(0.02)
        reader, writer = await asyncio.open_unix_connection(str(sock))
        writer.write(hello("tty-starved", 100, 30))
        await writer.drain()
        await asyncio.sleep(3)          # boot, with nothing being flushed

        writer.write(encode_data(CTRL_Q))
        await writer.drain()

        decoder = FrameDecoder()
        tail = bytearray()
        try:
            async with asyncio.timeout(10):
                while True:
                    chunk = await reader.read(65536)
                    if not chunk:
                        break
                    for kind, payload in decoder.feed(chunk):
                        if kind == "D":
                            tail.extend(payload)
        except TimeoutError:
            pass

        assert b"?1049l" in bytes(tail), (
            "with the flusher starved, the terminal restore never reached "
            f"the client: {len(tail)} bytes arrived")
        writer.close()
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=60)
