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
DOCK_WIDTH = 26


class PlanDock(Static):
    DEFAULT_CSS = f"""
    PlanDock {{
        width: {DOCK_WIDTH};
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
        self.update(render_plan_dock(
            self._state, self._palette, working=self._working,
            frame=self._frame, width=DOCK_WIDTH - 2,
            subplans=self._subplans))
