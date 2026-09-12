"""A client that attaches must get a usable screen, not a tab bar.

Every other test in this directory checks that bytes arrive. Bytes arrived
here too: 12.8 KB of them, a correct tab bar, and an otherwise blank
screen. Alex got a single tab on top and no input box, and the suite was
green.

The cause is a race the code already documents. A bridged `_spawn` mounts
its pane directly with `foreground=True`, and the brain's session observer
mounts the same pane through `run_worker`. Whichever arrives second
no-ops on `_mount_brain_pane`'s `pane_for` guard, which is by design. What
was not by design is that `_on_brain_session` passes no `foreground`, so
the observer route defaults to False: it mounts the pane hidden and never
points the ContentSwitcher at it. Under `run_test` the direct call wins
and everything looks fine. Under the real ViewDriver the worker wins.

So this asserts on the rendered bytes, which is the only place the
difference shows.
"""
import asyncio
import re

import pytest

from aegis.cli import _serve
from aegis.config import Agent
from aegis.config.roots import AegisRoots
from aegis.daemon import lifecycle
from aegis.daemon.protocol import FrameDecoder, hello
from aegis.events import AssistantText, Result, SystemInit

from tests.views.conftest import FakeMCP


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


async def _screen_of(roots, *, seconds: float = 6.0) -> str:
    sock = lifecycle.socket_path(roots)
    async with asyncio.timeout(25):
        while not sock.exists():
            await asyncio.sleep(0.02)
    reader, writer = await asyncio.open_unix_connection(str(sock))
    writer.write(hello("tty-test", 100, 30))
    await writer.drain()
    decoder = FrameDecoder()
    out = bytearray()
    try:
        async with asyncio.timeout(seconds):
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    break
                for kind, payload in decoder.feed(chunk):
                    if kind == "D":
                        out.extend(payload)
    except TimeoutError:
        pass
    writer.close()
    text = out.decode("utf-8", "replace")
    return re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", text)


async def test_an_attached_client_can_see_the_input_box(tmp_path):
    roots = AegisRoots.for_project(tmp_path)
    stop = asyncio.Event()
    task = asyncio.create_task(_serve(
        roots=roots, agents=_roster(), default_agent="opus",
        make_session=lambda agent, url, handle, **kw: _FakeHarness(),
        mcp=FakeMCP(), stop=stop, views=True))
    try:
        screen = await _screen_of(roots)
        # The tab bar alone is what the broken screen had, so asserting on
        # it would have passed throughout. The input box is the thing whose
        # absence made the session unusable.
        assert "type a message" in screen, (
            "attached client got a screen with no input box; "
            f"{len(screen)} chars rendered")
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=60)
