"""PlanStrip — always-on, one-line plan summary.

Sits above the status bar alongside QueueStrip and MonitorStrip, hidden
while the session has no plan. The spinner ticks only while the session is
mid-turn, which is the same condition under which working time accrues.
"""
from __future__ import annotations

from textual.widgets import Static

from aegis.plan.models import PlanState
from aegis.plan.render import render_plan_strip

_TICK = 0.25


class PlanStrip(Static):
    """Hidden (display:none) until the session has a plan."""

    def __init__(self, palette, **kw) -> None:
        super().__init__("", **kw)
        self._palette = palette
        self._state = PlanState()
        self._working = False
        self._frame = 0
        self.display = False

    def on_mount(self) -> None:
        self.set_interval(_TICK, self._tick)

    def _tick(self) -> None:
        # Repaint only while a task is actually running: a settled plan is
        # a static line and must not burn a redraw four times a second.
        if self._working and self._state.current is not None:
            self._frame += 1
            self._paint()

    def refresh_plan(self, state: PlanState, working: bool) -> None:
        self._state, self._working = state, working
        self.display = bool(state)
        self._paint()

    def on_resize(self) -> None:
        """Repaint at the real width — the label is truncated to fit, so a
        stale width leaves it cut short (or wrapping) after a resize."""
        self._paint()

    def _paint(self) -> None:
        # `or None` matters: a widget that has not been laid out yet reports
        # width 0, and a 0-column budget would truncate the label away
        # entirely. Unbounded until the first resize is the safe fallback.
        self.update(render_plan_strip(
            self._state, self._palette,
            working=self._working, frame=self._frame,
            width=self.size.width or None))
