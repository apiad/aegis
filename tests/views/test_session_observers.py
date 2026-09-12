"""SessionManager announces its session set.

A view learns what tabs exist by subscribing to the brain. Nothing did that
before: the only observer the manager installed was the per-session event
log (`manager.py:251-253`).
"""
import pathlib

from aegis.config import Agent
from aegis.config.roots import AegisRoots
from aegis.core.manager import SessionManager


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


def _mgr(tmp_path: pathlib.Path) -> SessionManager:
    roots = AegisRoots.for_project(tmp_path)
    return SessionManager({"default": _agent()}, "default",
                          make_session=lambda p, u, h, **kw: _FakeHarness(),
                          mcp=None, roots=roots)


async def test_spawn_announces_the_session(tmp_path):
    seen = []
    mgr = _mgr(tmp_path)
    mgr.add_session_observer(lambda kind, s: seen.append((kind, s.handle)))
    h = await mgr.spawn("default")
    assert seen == [("added", h)]


async def test_close_announces_the_removal(tmp_path):
    seen = []
    mgr = _mgr(tmp_path)
    h = await mgr.spawn("default")
    mgr.add_session_observer(lambda kind, s: seen.append((kind, s.handle)))
    await mgr.close(h)
    assert seen == [("removed", h)]


async def test_the_observer_sees_the_session_itself_not_a_copy(tmp_path):
    """Every view mounts a pane over THIS object. A copy would give each
    view its own AgentSession wrapping one harness — two transcripts, two
    pending queues, and a message cancelled in one view still live in the
    other."""
    seen = []
    mgr = _mgr(tmp_path)
    mgr.add_session_observer(lambda kind, s: seen.append(s))
    h = await mgr.spawn("default")
    assert seen[0] is mgr.get(h)


async def test_every_observer_fires_not_just_the_last(tmp_path):
    """The whole point is N views. A single-slot callback passes a
    one-observer test and fails this plan entirely."""
    a, b = [], []
    mgr = _mgr(tmp_path)
    mgr.add_session_observer(lambda kind, s: a.append(kind))
    mgr.add_session_observer(lambda kind, s: b.append(kind))
    await mgr.spawn("default")
    assert a == ["added"], "first observer stopped firing"
    assert b == ["added"], "second observer never fired"


async def test_a_broken_observer_does_not_take_the_brain_down(tmp_path):
    """One view with a bug must not stop the daemon from spawning. The
    surviving observer must still fire, so the failure cannot be
    'everything after the raise is skipped' either."""
    after = []
    mgr = _mgr(tmp_path)

    def _boom(kind, s):
        raise RuntimeError("this view is broken")

    mgr.add_session_observer(_boom)
    mgr.add_session_observer(lambda kind, s: after.append(kind))
    h = await mgr.spawn("default")
    assert h, "a broken observer killed the spawn"
    assert after == ["added"], "a broken observer starved the ones after it"
