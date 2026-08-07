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
    """Hidden via the ``-empty`` class until the session has a plan.

    A class, not ``self.display``: an imperative display is an *inline
    style* and beats CSS, so the sidebar's
    ``ConversationPane.-sidebar PlanStrip { display: none }`` would be
    silently overridden on the next plan update and the strip would
    reappear underneath the open sidebar. QueueStrip and MonitorStrip
    already used the class idiom; this one now matches them.
    """

    # The shared box model of the strips above the status bar — the same
    # rule QueueStrip and MonitorStrip carry. Without it this one rendered
    # transparent, hard against the left edge and flush to the status bar,
    # which read as a different kind of thing rather than one of the strips.
    DEFAULT_CSS = """
    PlanStrip { height: 1; padding: 0 2; margin-bottom: 1;
                background: $panel; color: $foreground; }
    PlanStrip.-empty { display: none; }
    """

    def __init__(self, palette, **kw) -> None:
        super().__init__("", **kw)
        self._palette = palette
        self._state = PlanState()
        self._working = False
        self._frame = 0
        self.add_class("-empty")

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
        self.set_class(not state, "-empty")
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
