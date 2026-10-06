"""A new tab opens in front, whichever route mounted its pane.

Two routes mount a brain pane and they race by design: the spawn that
asked for the tab, and the brain's session observer, which mounts through
``run_worker`` and never asks for the foreground. In a daemon view the
observer wins, and the spawn's request used to be dropped on the floor by
the ``pane_for`` guard -- Ctrl+T opened its tab behind the current one and
the next thing typed went to the old tab.

``run_test`` loses that race the other way, so a test that only presses
Ctrl+T passes on the broken build. The order is pinned here instead.
"""
import pytest
from aegis.config import Agent
from aegis.config.roots import AegisRoots
from aegis.views.registry import ViewRegistry
from textual.widgets import ContentSwitcher

from tests.brain import make_brain
from tests.views.conftest import FakeMCP


class _FakeHarness:
    async def start(self): ...
    async def send(self, t): ...
    async def close(self): ...

    async def events(self):
        if False:
            yield


def _agent():
    return Agent(harness="claude-code", model="opus",
                 effort="high", permission="auto")


def _reg(tmp_path):
    roots = AegisRoots.for_project(tmp_path)
    roster = {"default": _agent()}
    mgr = make_brain(roster, "default",
                     make_session=lambda p, u, h, **kw: _FakeHarness(),
                     mcp=None, roots=roots)
    reg = ViewRegistry(manager=mgr, roots=roots, mcp=FakeMCP(),
                       agents=roster, default_agent="default",
                       make_session=lambda p, u, h, **kw: _FakeHarness())
    return reg, mgr


async def test_a_foreground_mount_still_foregrounds_a_pane_already_there(tmp_path):
    """The observer mounts first; the spawn's foreground must still land."""
    reg, mgr = _reg(tmp_path)
    v = await reg.open("solo", (100, 30))
    app = v.app
    async with app.run_test(headless=False, size=(100, 30)) as pilot:
        await pilot.pause()
        cs = app.query_one(ContentSwitcher)
        first = cs.current
        assert first is not None, "the view opened showing nothing"

        sess = mgr._sync_spawn("default")
        # The observer's mount: no foreground, as `_on_brain_session` does.
        await app._mount_brain_pane(sess)
        await pilot.pause()
        assert cs.current == first, "a background mount stole the front"

        # The spawn's mount, arriving second.
        await app._mount_brain_pane(sess, foreground=True)
        await pilot.pause()
        pane = app.pane_for(sess.handle)
        assert cs.current == pane.id, (
            f"front is {cs.current}, not the new tab {pane.id}")
        assert app.focused in pane.walk_children(with_self=True), (
            f"focus is on {app.focused!r}, outside the new tab")
    await reg.close_all()
