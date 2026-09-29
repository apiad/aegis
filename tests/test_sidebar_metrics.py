"""CONTEXT's per-turn figures, in a running app.

Assertions are on what the widget actually painted, not on the model field
being set: a model assertion is green against a section that was never
composed into `SECTIONS`. Same seam as `tests/test_sidebar_system.py`.
"""
from __future__ import annotations

import pytest

from aegis.config import Agent
from aegis.events import Result
from aegis.tui.app import AegisApp
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


def _app():
    def make(agent, mcp_url, handle, **kw):
        return FakeSession()
    return AegisApp({"default": _agent()}, "default", make, FakeMCP())


def _painted(pane) -> str:
    sidebar = pane.query_one(Sidebar)
    assert sidebar._paints > 0, "the sidebar never painted"
    return sidebar.plain()


def _generated_a_turn(core, *, out: int, seconds: float) -> None:
    """Drive the metrics model the way a finished generation turn does.

    `recent_tps` samples only turns with positive output over a measured
    duration (`metrics.py:228-230`), so both have to be real.
    """
    m = core.metrics
    m._turn_rates.append((out, seconds))
    m.c_out += out
    m.c_in += out * 10
    m.c_cached += out * 8
    m.c_think += out // 2
    m.tool_calls += 3
    m.tool_errors += 1
    m.compaction_count += 1
    m.last_true_input = out * 10
    m.context_window = out * 100


@pytest.mark.asyncio
async def test_the_sidebar_shows_generation_speed_beside_its_own_gauge():
    """The regression in #12: drawing the CTX bar cost the row five figures.

    `context_window` is set, so `MetricsModel.gauge()` returns a reading and
    `_context` takes the branch that used to fall back to tier 3.
    """
    app = _app()
    async with app.run_test(size=(120, 40)) as pilot:
        pane = app._panes[0]
        _generated_a_turn(pane._core, out=600, seconds=6.0)
        pane.toggle_task_dock()
        await pilot.pause()

        painted = _painted(pane)
        assert pane._core.metrics.gauge() is not None, "no gauge, wrong branch"
        assert "tok/s" in painted
        # 600 output tokens over 6.0s. Derived from the same inputs the helper
        # fed in rather than written as a literal, so a changed helper cannot
        # leave a stale expectation passing.
        assert f"{round(600 / 6.0)} tok/s" in painted


@pytest.mark.asyncio
async def test_the_sidebar_keeps_the_four_figures_tier_three_drops():
    """All five figures, at a width that holds them.

    Rendered at 56 cells rather than asserted off the painted column: the
    sidebar is 36 cells wide at a 120-wide terminal, the five measure ~41 on
    one row, and `fit_rows` would correctly narrow them. The claim under test
    is that the figures reach the renderer, not that they fit any column.
    """
    from aegis.tui.sidebar import render_sidebar

    app = _app()
    async with app.run_test(size=(120, 40)) as pilot:
        pane = app._panes[0]
        _generated_a_turn(pane._core, out=600, seconds=6.0)
        pane.toggle_task_dock()
        await pilot.pause()

        sidebar = pane.query_one(Sidebar)
        assert sidebar._paints > 0, "the sidebar never painted"
        wide = render_sidebar(sidebar._model, sidebar._palette, 56).plain
        assert "tok/s" in wide
        assert "cached" in wide
        assert "think" in wide
        assert "⚒" in wide
        assert "✂" in wide


@pytest.mark.asyncio
async def test_a_narrow_column_sheds_the_shares_and_keeps_the_speed():
    """Speed is the one figure with no other surface in this column, so it is
    first in its segment and survives into the narrowest tier."""
    from aegis.tui.sidebar import render_sidebar

    app = _app()
    async with app.run_test(size=(120, 40)) as pilot:
        pane = app._panes[0]
        _generated_a_turn(pane._core, out=600, seconds=6.0)
        pane.toggle_task_dock()
        await pilot.pause()

        sidebar = pane.query_one(Sidebar)
        narrow = render_sidebar(sidebar._model, sidebar._palette, 18).plain
        assert "tok/s" in narrow
        assert "cached" not in narrow


@pytest.mark.asyncio
async def test_a_session_with_no_completed_turn_shows_no_speed_row():
    """`recent_tps()` is None before any generation turn, and a row that
    renders `0 tok/s` there would claim a measurement nobody made."""
    app = _app()
    async with app.run_test(size=(120, 40)) as pilot:
        pane = app._panes[0]
        pane.toggle_task_dock()
        await pilot.pause()
        assert "tok/s" not in _painted(pane)
