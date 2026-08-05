"""Pure plan renderers — the strip and the dock.

The circle glyphs are East Asian Ambiguous width: Rich measures them as
one cell, many terminals draw them wider, and adjacent circles visibly
overlap. The no-adjacent-circles test below is the regression guard, and
it is asserted on the rendered string rather than left as a convention in
a doc, because a note decays and a test does not.
"""
import re

import pytest

from aegis.plan import PlanState, PlanTask
from aegis.plan.render import (
    SPINNER_FRAMES, fmt_working, render_plan_dock, render_plan_strip,
)
from aegis.tui.themes import INK, aegis_colors

C = aegis_colors(INK)          # house pattern — see tests/test_render_event.py
CIRCLES = "●◐○◓◑◒"


def as_text(renderable) -> str:
    """`.plain`, not a Rich Console: the adjacency assertion must see the
    exact string the renderer produced, with no wrapping or padding."""
    return renderable.plain


def state(*pairs, times=None):
    times = times or {}
    return PlanState(tasks=tuple(
        PlanTask(key=str(i), subject=s, status=st, working_s=times.get(s))
        for i, (s, st) in enumerate(pairs)))


# -- the overlap guard -----------------------------------------------

def test_no_two_circles_are_ever_adjacent():
    s = state(*[(f"t{i}", "completed") for i in range(5)])
    for out in (as_text(render_plan_strip(s, C)),
                as_text(render_plan_dock(s, C))):
        assert not re.search(f"[{CIRCLES}][{CIRCLES}]", out), repr(out)


def test_no_two_circles_are_adjacent_in_a_mixed_windowed_plan():
    pairs = [(f"t{i}", "completed") for i in range(13)]
    pairs.append(("cur", "in_progress"))
    pairs += [(f"u{i}", "pending") for i in range(17)]
    out = as_text(render_plan_strip(state(*pairs), C, working=True, frame=1))
    assert not re.search(f"[{CIRCLES}][{CIRCLES}]", out), repr(out)


# -- the strip -------------------------------------------------------

def test_strip_has_one_circle_per_task_in_plan_order():
    s = state(("a", "completed"), ("b", "completed"),
              ("c", "in_progress"), ("d", "pending"))
    out = as_text(render_plan_strip(s, C))
    assert "● ● ◐ ○" in out
    assert "2/4" in out


def test_strip_shows_the_current_task_label_and_clock():
    s = state(("a", "completed"), ("b", "in_progress"), times={"b": 63.0})
    out = as_text(render_plan_strip(s, C))
    assert "b" in out and "1:03" in out


def test_strip_windows_around_the_current_task_past_the_cap():
    """31 tasks, current at index 13, cap 12 → window is tasks[7:19], so
    both sides elide. The count stays honest regardless."""
    pairs = [(f"t{i}", "completed") for i in range(13)]
    pairs.append(("cur", "in_progress"))
    pairs += [(f"u{i}", "pending") for i in range(17)]
    out = as_text(render_plan_strip(state(*pairs), C, cap=12))
    assert out.count("…") == 2
    assert len(re.findall(f"[{CIRCLES}]", out)) == 12
    assert "13/31" in out


def test_window_elides_only_the_side_that_needs_it():
    pairs = [("cur", "in_progress")]
    pairs += [(f"u{i}", "pending") for i in range(20)]
    out = as_text(render_plan_strip(state(*pairs), C, cap=12))
    assert out.count("…") == 1
    assert out.index("…") > out.index("◐")     # trailing, not leading


def test_a_plan_at_exactly_the_cap_does_not_elide():
    s = state(*[(f"t{i}", "pending") for i in range(12)])
    assert "…" not in as_text(render_plan_strip(s, C, cap=12))


def test_empty_plan_renders_empty():
    assert as_text(render_plan_strip(PlanState(), C)) == ""


def test_spinner_advances_only_when_working():
    """The circle spins iff working time is accruing — the rotation is a
    literal rendering of the clock running."""
    s = state(("a", "in_progress"))
    assert "◐" in as_text(render_plan_strip(s, C, working=False, frame=0))
    frames = {as_text(render_plan_strip(s, C, working=True, frame=f))
              for f in range(len(SPINNER_FRAMES))}
    assert len(frames) == len(SPINNER_FRAMES)


def test_a_settled_plan_looks_the_same_on_every_frame():
    s = state(("a", "completed"), ("b", "pending"))
    outs = {as_text(render_plan_strip(s, C, working=True, frame=f))
            for f in range(4)}
    assert len(outs) == 1


# -- formatting ------------------------------------------------------

def test_fmt_working():
    assert fmt_working(None) == "—"
    assert fmt_working(0.0) == "0:00"
    assert fmt_working(63.0) == "1:03"
    assert fmt_working(3723.0) == "1:02:03"


# -- the dock --------------------------------------------------------

def test_dock_row_per_task_with_glyph_and_time():
    s = state(("explore", "completed"), ("clarify", "in_progress"),
              times={"explore": 252.0, "clarify": 63.0})
    out = as_text(render_plan_dock(s, C))
    assert "● explore" in out and "4:12" in out
    assert "◐ clarify" in out and "1:03" in out


def test_never_started_task_shows_a_dash_not_a_zero():
    assert "—" in as_text(render_plan_dock(state(("a", "pending")), C))


def test_dock_truncates_a_long_label_rather_than_wrapping():
    s = state(("x" * 200, "pending"))
    for line in as_text(render_plan_dock(s, C, width=24)).splitlines():
        assert len(line) <= 40


def test_dock_nests_subagent_plans_under_the_top_level_plan():
    """Nesting is what makes a fan-out legible — it shows which of three
    parallel agents is still grinding."""
    top = state(("dispatch", "in_progress"))
    sub = state(("grind", "in_progress"), ("finished", "completed"))
    out = as_text(render_plan_dock(top, C, subplans={"tool_1": sub}))
    assert "dispatch" in out and "subagent 1/2" in out
    assert "    ◐ grind" in out
    assert not re.search(f"[{CIRCLES}][{CIRCLES}]", out)


def test_dock_with_no_plan_at_all():
    assert "no plan" in as_text(render_plan_dock(PlanState(), C))


# -- widgets ---------------------------------------------------------

@pytest.mark.asyncio
async def test_plan_strip_hides_when_there_is_no_plan():
    from textual.app import App, ComposeResult

    from aegis.tui.plan_strip import PlanStrip

    class _A(App):
        def compose(self) -> ComposeResult:
            yield PlanStrip(C, id="plan-strip")

    async with _A().run_test() as pilot:
        strip = pilot.app.query_one("#plan-strip", PlanStrip)
        assert strip.display is False
        strip.refresh_plan(state(("a", "in_progress")), working=True)
        assert strip.display is True


@pytest.mark.asyncio
async def test_plan_dock_toggles_and_renders_rows():
    from textual.app import App, ComposeResult

    from aegis.tui.plan_dock import PlanDock

    class _A(App):
        def compose(self) -> ComposeResult:
            yield PlanDock(C, id="plan-dock")

    async with _A().run_test() as pilot:
        dock = pilot.app.query_one("#plan-dock", PlanDock)
        assert dock.display is False
        dock.refresh_plan(state(("a", "in_progress")), {}, working=True)
        assert dock.toggle() is True
        assert dock.display is True
        assert dock.toggle() is False


# -- pane wiring -----------------------------------------------------


class _Gated:
    """Mirrors tests/test_pane_input_state_outline.py's GatedSession."""

    def __init__(self):
        self.sent: list[str] = []
        self.started = self.closed = False

    async def start(self):
        self.started = True

    async def send(self, text):
        self.sent.append(text)

    async def events(self):
        from aegis.events import Result
        yield Result(duration_ms=1, is_error=False, usage=None)

    async def close(self):
        self.closed = True


class _FakeMCP:
    url = "http://127.0.0.1:0/mcp/"

    def bind(self, bridge):
        pass

    async def start(self):
        pass

    async def stop(self):
        pass


def _app():
    from aegis.config import Agent
    from aegis.tui.app import AegisApp

    agent = Agent(harness="claude-code", model="opus", effort="high",
                  permission="auto")
    return AegisApp({"default": agent}, "default",
                    lambda a, u, h: _Gated(), _FakeMCP())


@pytest.mark.asyncio
async def test_pane_feeds_the_strip_from_the_session_plan():
    """The widget tests above prove the widget. This proves the wiring:
    an AgentPlan arriving on the core session must reach the strip."""
    from aegis.events import AgentPlan, PlanEntry
    from aegis.tui.plan_strip import PlanStrip

    async with _app().run_test() as pilot:
        pane = pilot.app._panes[0]
        strip = pane.query_one("#plan-strip", PlanStrip)
        assert strip.display is False

        pane._core._fire_event(AgentPlan(entries=(
            PlanEntry(content="explore", status="completed"),
            PlanEntry(content="build", status="in_progress"),
        )))
        await pilot.pause()

        assert strip.display is True
        out = strip.render().plain
        assert "1/2" in out and "build" in out


@pytest.mark.asyncio
async def test_a_subagent_plan_does_not_change_the_strip():
    """The strip is flat and top-level-only: merging several agents'
    lists into one line is noise."""
    from aegis.events import AgentPlan, PlanEntry
    from aegis.tui.plan_strip import PlanStrip

    async with _app().run_test() as pilot:
        pane = pilot.app._panes[0]
        strip = pane.query_one("#plan-strip", PlanStrip)
        pane._core._fire_event(AgentPlan(entries=(
            PlanEntry(content="a", status="completed"),
            PlanEntry(content="b", status="in_progress"),
            PlanEntry(content="c", status="pending"))))
        await pilot.pause()
        pane._core._fire_event(AgentPlan(
            entries=(PlanEntry(content="sub", status="in_progress"),),
            parent_tool_use_id="tool_1"))
        await pilot.pause()

        assert "1/3" in strip.render().plain
