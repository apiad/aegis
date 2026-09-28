"""The NETWORK rows, in a running app.

Assertions read what the widget painted, never the model field: a model
assertion is green against a section that was never composed into
`SECTIONS`, which is what this file exists to catch. Same seam as
`tests/test_sidebar_system.py`.
"""
from __future__ import annotations

import pytest

from aegis.config import Agent, NetworkConfig
from aegis.events import Result
from aegis.net.probe import Reach, Trace
from aegis.net.service import NetState
from aegis.tui.app import AegisApp
from aegis.tui.fit import strip_markup
from aegis.tui.sidebar import Sidebar


def _agent():
    return Agent(harness="claude-code", model="opus",
                 effort="high", permission="auto")


class FakeSession:
    session_id = "sid-1"

    def __init__(self):
        self.sent = []

    async def start(self): pass
    async def send(self, text): self.sent.append(text)

    async def events(self):
        yield Result(duration_ms=1, is_error=False)

    async def close(self): pass


class FakeMCP:
    url = "http://127.0.0.1:0/mcp/"

    def bind(self, bridge): self.bound = bridge
    async def start(self): pass
    async def stop(self): pass


def _app(network=None):
    def make(agent, mcp_url, handle, **kw):
        return FakeSession()
    return AegisApp(
        {"default": _agent()}, "default", make, FakeMCP(), network=network
    )


def _painted(pane) -> str:
    sidebar = pane.query_one(Sidebar)
    assert sidebar._paints > 0, "the sidebar never painted"
    return sidebar.plain()


def _live_state():
    return NetState(
        reach=Reach(ok=True, rtt_ms=18.0), reach_at=0.0,
        trace=Trace(ok=True, ip="2a0d:5600:6:202::15", colo="MIA"), trace_at=0.0,
    )


@pytest.mark.asyncio
async def test_the_system_block_carries_the_network_rows():
    app = _app()
    async with app.run_test(size=(120, 40)) as pilot:
        pane = app._panes[0]
        pane.toggle_task_dock()
        pane.set_net(_live_state())
        await pilot.pause()

        painted = _painted(pane)
        assert "SYSTEM" in painted
        assert "NET" in painted
        assert "18ms" in painted
        assert "2a0d:5600:6:202::15" in painted


@pytest.mark.asyncio
async def test_a_dead_link_is_visible_in_the_painted_column():
    app = _app()
    async with app.run_test(size=(120, 40)) as pilot:
        pane = app._panes[0]
        pane.toggle_task_dock()
        pane.set_net(NetState(reach=Reach(ok=False, error="unreachable"),
                              reach_at=0.0))
        await pilot.pause()
        assert "no egress" in _painted(pane)


@pytest.mark.asyncio
async def test_the_network_rows_sit_above_the_clock_row():
    """SECTIONS orders by volatility and `_system` applies it one level down:
    the RTT moves every 20s, the clock every minute, cwd and build never."""
    app = _app()
    async with app.run_test(size=(120, 40)) as pilot:
        pane = app._panes[0]
        pane.toggle_task_dock()
        pane.set_net(NetState(reach=Reach(ok=True, rtt_ms=18.0), reach_at=0.0))
        await pilot.pause()

        rows = _painted(pane).split("\n")
        net_at = next(i for i, r in enumerate(rows) if "NET" in r)
        cwd_at = next(i for i, r in enumerate(rows) if "CWD" in r)
        assert net_at < cwd_at


@pytest.mark.asyncio
async def test_a_narrow_column_keeps_the_reading_and_drops_the_address():
    """`fit_rows` drops a segment whose narrowest tier overflows. The address
    is the half with another surface (`/net`); the reading is not."""
    from aegis.tui.sidebar import render_sidebar

    app = _app()
    async with app.run_test(size=(120, 40)) as pilot:
        pane = app._panes[0]
        pane.toggle_task_dock()
        pane.set_net(_live_state())
        await pilot.pause()
        sidebar = pane.query_one(Sidebar)

        # One cell narrower than the address row needs, derived rather than
        # guessed: at a round 24 the indented address measures exactly 24 and
        # fits, which made an earlier version of this test assert nothing.
        exit_row = sidebar._model.exit_ip[0]
        width = len(strip_markup(exit_row)) - 1
        narrow = render_sidebar(sidebar._model, sidebar._palette, width).plain
        assert "18ms" in narrow, "the reading must survive the narrowest column"
        assert "2a0d:5600:6:202::15" not in narrow


@pytest.mark.asyncio
async def test_disabled_probing_builds_no_service_and_paints_no_rows():
    """I6, rewritten after review.

    The earlier version passed `network=None`, which becomes
    `NetworkConfig(enabled=True)` — so it never exercised the disabled branch
    at all, and what it actually asserted was that the 1s `_tick` had not
    fired yet. It went red under a 1.2s pause. This drives the tick on purpose
    and names the config it is testing.
    """
    app = _app(network=NetworkConfig(enabled=False))
    async with app.run_test(size=(120, 40)) as pilot:
        pane = app._panes[0]
        pane.toggle_task_dock()
        app._tick()
        await pilot.pause()

        assert app.net_service is None, "a disabled config still built a service"
        painted = _painted(pane)
        assert "SYSTEM" in painted
        assert "NET" not in painted


@pytest.mark.asyncio
async def test_the_probe_loop_does_not_outlive_the_app():
    """I4. The teardown lived only in `action_quit`; Textual unmounts on every
    shutdown path, and a leaked loop holds the whole app alive through its
    bound method."""
    app = _app()
    async with app.run_test(size=(120, 40)) as pilot:
        app._tick()
        assert app.net_service.started, "the tick did not start the service"
    assert not app.net_service.started, "the probe loop outlived the app"
