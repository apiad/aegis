"""PlanDock — the toggled right-hand task panel.

Hidden by default: it costs ~26 columns, which is the right trade only on
a wide terminal. The strip is the always-on surface; this is the
drill-down, and the place where subagent plans nest.
"""
from __future__ import annotations

from textual.widgets import Static

from aegis.plan.models import PlanState
from aegis.plan.render import render_plan_dock

_TICK = 0.25
# Proportional, not a magic number. Across 344 real task subjects the
# median is 35 characters and p90 is 49, so no fixed width works: 26
# columns truncates 99% of them, and fitting 90% would need a 59-column
# dock — half a laptop screen. A share of the pane adapts instead, and the
# bounds keep it useful on an 80-col terminal without eating a 200-col one.
DOCK_PCT = 34
DOCK_MIN = 26
DOCK_MAX = 60


class PlanDock(Static):
    DEFAULT_CSS = f"""
    PlanDock {{
        width: {DOCK_PCT}%;
        min-width: {DOCK_MIN};
        max-width: {DOCK_MAX};
        padding: 0 1;
    }}
    """

    def __init__(self, palette, **kw) -> None:
        super().__init__("", **kw)
        self._palette = palette
        self._state = PlanState()
        self._subplans: dict = {}
        self._working = False
        self._frame = 0
        self._open = False
        self.display = False

    def on_mount(self) -> None:
        self.set_interval(_TICK, self._tick)

    def on_resize(self) -> None:
        """Repaint at the real width.

        toggle() paints immediately, but a widget that was display:none has
        not been laid out yet — size.width is still 0, so the first paint
        falls back to DOCK_MIN and truncates labels far harder than the
        dock's actual width requires. This also covers a terminal resize.
        """
        if self._open:
            self._paint()

    def _tick(self) -> None:
        if self._open and self._working and self._state.current is not None:
            self._frame += 1
            self._paint()

    def toggle(self) -> bool:
        self._open = not self._open
        self.display = self._open
        if self._open:
            self._paint()
        return self._open

    @property
    def is_open(self) -> bool:
        return self._open

    def refresh_plan(self, state: PlanState, subplans: dict,
                     working: bool) -> None:
        self._state, self._subplans, self._working = state, subplans, working
        if self._open:
            self._paint()

    def _paint(self) -> None:
        # Measure at paint time: the dock is a share of the pane, so its
        # width changes when the terminal is resized.
        w = self.size.width or DOCK_MIN
        self.update(render_plan_dock(
            self._state, self._palette, working=self._working,
            frame=self._frame, width=max(DOCK_MIN, w) - 2,
            subplans=self._subplans))
