"""The input direction of the view seam: client bytes -> app messages.

Stage 4 built frames-out and left frames-in unbuilt. These tests drive a
real view the way a transport will -- by handing it bytes -- rather than
with pilot.press, which posts straight to the app and would pass against a
`feed` that did nothing at all.
"""
import json

from aegis.config import Agent
from aegis.config.roots import AegisRoots
from aegis.core.manager import SessionManager
from aegis.views.registry import ViewRegistry

from tests.views.conftest import FakeMCP


class _FakeHarness:
    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...

    async def events(self):
        if False:
            yield


def _reg(tmp_path):
    roots = AegisRoots.for_project(tmp_path)
    roster = {"default": Agent(harness="claude-code", model="opus",
                               effort="high", permission="auto")}
    mgr = SessionManager(roster, "default",
                         make_session=lambda p, u, h: _FakeHarness(),
                         mcp=None, roots=roots)
    return ViewRegistry(manager=mgr, roots=roots, mcp=FakeMCP()), mgr


def _data(payload: bytes) -> bytes:
    return b"D" + len(payload).to_bytes(4, "big") + payload


def _meta(obj: dict) -> bytes:
    raw = json.dumps(obj).encode("utf-8")
    return b"M" + len(raw).to_bytes(4, "big") + raw


async def test_a_data_frame_becomes_a_key_event(tmp_path):
    reg, _ = _reg(tmp_path)
    view = await reg.open("v1", (80, 24))
    async with view.app.run_test(headless=False, size=(80, 24)) as pilot:
        await pilot.pause()
        seen = []
        view.app._driver.process_message = lambda ev: seen.append(ev)
        view.app._driver.feed(_data(b"x"))
        assert [getattr(e, "key", None) for e in seen] == ["x"]


async def test_a_data_frame_split_across_chunks_still_arrives(tmp_path):
    """A socket splits wherever it likes; the header may arrive alone."""
    reg, _ = _reg(tmp_path)
    view = await reg.open("v1", (80, 24))
    async with view.app.run_test(headless=False, size=(80, 24)) as pilot:
        await pilot.pause()
        seen = []
        view.app._driver.process_message = lambda ev: seen.append(ev)
        frame = _data(b"hi")
        for i in range(len(frame)):
            view.app._driver.feed(frame[i:i + 1])
        assert [getattr(e, "key", None) for e in seen] == ["h", "i"]


async def test_a_resize_meta_frame_resizes_this_view(tmp_path):
    reg, _ = _reg(tmp_path)
    view = await reg.open("v1", (80, 24))
    async with view.app.run_test(headless=False, size=(80, 24)) as pilot:
        await pilot.pause()
        view.app._driver.feed(_meta({"type": "resize",
                                     "width": 120, "height": 40}))
        await pilot.pause()
        assert tuple(view.app._driver._size) == (120, 40)


async def test_a_damaged_meta_frame_does_not_kill_the_view(tmp_path):
    """A client is untrusted input. A bad frame costs that frame, not the
    daemon -- every other view in the process is on this same loop."""
    reg, _ = _reg(tmp_path)
    view = await reg.open("v1", (80, 24))
    async with view.app.run_test(headless=False, size=(80, 24)) as pilot:
        await pilot.pause()
        bad = b"nope"
        view.app._driver.feed(b"M" + len(bad).to_bytes(4, "big") + bad)
        seen = []
        view.app._driver.process_message = lambda ev: seen.append(ev)
        view.app._driver.feed(_data(b"y"))
        assert [getattr(e, "key", None) for e in seen] == ["y"]
