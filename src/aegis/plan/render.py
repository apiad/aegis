"""Pure plan renderers. Widgets stay dumb; these produce Rich Text and
are asserted directly in tests.

Circle glyphs come from render_shared.PLAN_STATUS_GLYPH — never a second
vocabulary — and are ALWAYS space-separated. They are East Asian
Ambiguous width: Rich measures them as one cell, many terminals draw the
glyph wider, and adjacent circles visibly overlap.
"""
from __future__ import annotations

from rich.cells import cell_len, set_cell_size
from rich.text import Text

from aegis.plan.models import PlanState
from aegis.render_shared import PLAN_STATUS_GLYPH

# Rotating half-fill, in the same idiom as render._TOOL_SPINNER. It
# advances only while the session is mid-turn, which is exactly when
# working time accrues — the spin IS the clock running.
SPINNER_FRAMES = "◐◓◑◒"

_STRIP_LABEL = "tasks: "

# glyph + space + label + space + a right-aligned clock. The clock column
# is what every row lines its right edge up on, so it is a constant and
# the label gets whatever is left.
_CLOCK_W = 6
_ROW_CHROME = 3 + _CLOCK_W      # glyph, the two spaces, the clock


def _fit(text: str, cells: int) -> str:
    """Truncate to `cells` display columns, ellipsis included in the
    budget. Measured in cells, never in characters: one emoji is one
    character and two columns, and a row padded by len() puts its clock a
    column out of line with every other row."""
    if cells <= 0:
        return ""
    if cell_len(text) <= cells:
        return text
    return "…" if cells == 1 else set_cell_size(text, cells - 1) + "…"


def fmt_working(seconds: float | None) -> str:
    """A task that never entered in_progress reads "—", not "0:00": the
    two mean different things and must not look alike."""
    if seconds is None:
        return "—"
    s = int(seconds)
    if s >= 3600:
        return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"
    return f"{s // 60}:{s % 60:02d}"


def _glyph(task, working: bool, frame: int) -> str:
    if task.status == "in_progress" and working:
        return SPINNER_FRAMES[frame % len(SPINNER_FRAMES)]
    return PLAN_STATUS_GLYPH.get(task.status, "○")


def _style(task, colors):
    return (colors.ok if task.status == "completed"
            else colors.accent if task.status == "in_progress"
            else colors.muted)


def _window(state: PlanState, cap: int) -> tuple[list, bool, bool]:
    """One circle per task up to `cap`, windowed around the current task.
    Returns (tasks, elided_left, elided_right)."""
    tasks = list(state.tasks)
    if len(tasks) <= cap:
        return tasks, False, False
    cur = next((i for i, t in enumerate(tasks)
                if t.status == "in_progress"), 0)
    start = max(0, min(cur - cap // 2, len(tasks) - cap))
    return tasks[start:start + cap], start > 0, start + cap < len(tasks)


def render_plan_strip(state: PlanState, colors, *, working: bool = False,
                      frame: int = 0, cap: int = 12,
                      width: int | None = None) -> Text:
    """One line: circle strip, count, current task, working clock.

    One circle per task rather than a fixed-width bar, because a plan is a
    small ordered set of discrete things — the strip is the task list in
    miniature and shows not just how far along but where.

    `width` is opt-in — omit it to measure, pass the pane width to paint.
    When given, the current label absorbs the whole shortfall: the circles
    and the count are the reason the strip exists, so a narrow pane drops
    the label and keeps the progress. Without it the strip is unbounded
    and a long `active_form` wraps the widget to two lines, which makes
    the transcript jump every time the current task changes.
    """
    out = Text()
    if not state:
        return out
    out.append(_STRIP_LABEL, style=colors.muted)
    window, left, right = _window(state, cap)
    if left:
        out.append("…", style=colors.muted)
    for i, task in enumerate(window):
        if i:
            out.append(" ")     # never adjacent — see the module docstring
        out.append(_glyph(task, working, frame), style=_style(task, colors))
    if right:
        out.append("…", style=colors.muted)
    out.append(f"  {state.done}/{state.total}", style=colors.ink)
    if cur := state.current:
        clock = fmt_working(cur.working_s)
        label = cur.label
        if width is not None:
            # Everything ahead of the label is already in `out`, so measure
            # it rather than re-deriving it — the separator and the clock
            # are all that still have to fit.
            label = _fit(label, width - cell_len(out.plain)
                         - cell_len(" · ") - 1 - cell_len(clock))
        if label:
            out.append(" · ", style=colors.muted)
            out.append(label, style=colors.ink)
            out.append(f" {clock}", style=colors.muted)
    return out


def _dock_row(out: Text, task, colors, working: bool, frame: int,
              label_w: int, indent: str) -> None:
    out.append(indent)
    out.append(_glyph(task, working, frame), style=_style(task, colors))
    label = set_cell_size(_fit(task.label, label_w), label_w)
    out.append(f" {label} ", style=colors.ink)
    out.append(f"{fmt_working(task.working_s):>{_CLOCK_W}}\n",
               style=colors.muted)


def render_plan_dock(state: PlanState, colors, *, working: bool = False,
                     frame: int = 0, width: int = 24,
                     subplans: dict | None = None) -> Text:
    """One row per task: glyph, label, working time. Subagent plans nest
    beneath their own header — that is what makes a fan-out legible, since
    it shows which of several parallel agents is still grinding."""
    if not state and not subplans:
        return Text("(no plan)", style=colors.muted)
    out = Text()
    out.append(f"tasks {state.done}/{state.total}\n",
               style=f"bold {colors.accent}")
    label_w = max(8, width - _ROW_CHROME)
    for task in state.tasks:
        _dock_row(out, task, colors, working, frame, label_w, indent="")
    for sub in (subplans or {}).values():
        if not sub:
            continue
        out.append(f"  └ subagent {sub.done}/{sub.total}\n",
                   style=colors.muted)
        for task in sub.tasks:
            # The four indent columns come out of the label, so a nested
            # row lands on the same right edge as a top-level one.
            _dock_row(out, task, colors, working, frame,
                      max(6, label_w - 4), indent="    ")
    return out
