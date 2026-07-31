"""A monitor must be visible in EVERY pane, however that pane was born.

The strip is composed once, at pane construction, and only when the pane
was handed a monitor manager — so a construction site that forgets to pass
one produces a tab that can never show a monitor for its whole life. These
tests drive the spawn/fork paths (``aegis_spawn``, ``/spawn``, group and
queue workers, ``/fork``) rather than the boot pane the other TUI tests use.
"""
from __future__ import annotations

import pytest
from aegis.config import Agent
from aegis.events import AssistantText, Result
from aegis.tui.app import AegisApp
from aegis.tui.monitor_strip import MonitorStrip


def _agent():
    return Agent(harness="claude-code", model="opus",
                 effort="high", permission="auto")


class FakeSession:
    # A real session only learns its id from the first SystemInit; fork's
    # guard refuses without one, so the fake is born with it.
    session_id = "sid-1"

    def __init__(self):
        self.sent = []
        self.started = self.closed = False

    async def start(self): self.started = True
    async def send(self, text): self.sent.append(text)

    async def events(self):
        yield AssistantText("ok")
        yield Result(duration_ms=1, is_error=False)

    async def close(self): self.closed = True


class FakeMCP:
    url = "http://127.0.0.1:0/mcp/"

    def bind(self, bridge): self.bound = bridge
    async def start(self): pass
    async def stop(self): pass


def _factory():
    # ``fork`` passes fork_from=…, plain spawn does not.
    def make(agent, mcp_url, handle, **kw):
        return FakeSession()
    return make


class _ForkableDriver:
    supports_fork = True


def _app():
    return AegisApp({"default": _agent()}, "default", _factory(), FakeMCP(),
                    drivers={"claude-code": _ForkableDriver()})


def _pane(app, handle):
    return next(p for p in app._panes if p.handle == handle)


@pytest.mark.asyncio
async def test_spawned_pane_shows_a_monitor_armed_for_it():
    """A peer spawned via aegis_spawn arms a monitor; its own tab must show
    it. This is the reported bug: the strip was simply never composed."""
    app = _app()
    async with app.run_test() as pilot:
        handle = await app.spawn("default", handle="peer-one")
        await pilot.pause()
        pane = _pane(app, handle)

        app.monitor_manager.start_monitor(
            from_handle=handle, description="full suite",
            done="false", autorun=False)
        await pilot.pause()

        strip = pane.query_one(MonitorStrip)
        assert not strip.has_class("-empty")
        assert "full suite" in str(strip.content)


@pytest.mark.asyncio
async def test_forked_pane_shows_a_monitor_armed_for_it():
    app = _app()
    async with app.run_test() as pilot:
        parent = app._panes[0].handle
        handle = await app.fork(parent, slug="fork-one")
        await pilot.pause()
        pane = _pane(app, handle)

        app.monitor_manager.start_monitor(
            from_handle=handle, description="forked watch",
            done="false", autorun=False)
        await pilot.pause()

        strip = pane.query_one(MonitorStrip)
        assert not strip.has_class("-empty")
        assert "forked watch" in str(strip.content)


@pytest.mark.asyncio
async def test_every_pane_has_a_strip_however_it_was_born():
    """The invariant, stated over the whole roster rather than per path.

    A pane's strip is composed once, from the manager the caller passed —
    so forgetting the kwarg at one construction site produces a tab that
    can never show a monitor, and the per-path tests above only cover the
    sites someone thought to test. Asserting over every pane in the app
    catches a new site the moment a test spawns through it."""
    app = _app()
    async with app.run_test() as pilot:
        await app.spawn("default", handle="peer-two")     # aegis_spawn
        await app.action_new_tab()                        # interactive ctrl+n
        await app.fork("peer-two", slug="fork-two")       # /fork
        await pilot.pause()

        assert len(app._panes) == 4                       # + the boot pane
        missing = [p.handle for p in app._panes
                   if not p.query(MonitorStrip)]
        assert missing == []
