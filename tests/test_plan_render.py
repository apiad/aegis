"""Pure plan renderers — the strip and the dock.

The circle glyphs are East Asian Ambiguous width: Rich measures them as
one cell, many terminals draw them wider, and adjacent circles visibly
overlap. The no-adjacent-circles test below is the regression guard, and
it is asserted on the rendered string rather than left as a convention in
a doc, because a note decays and a test does not.
"""
import re

import pytest
from rich.cells import cell_len

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
    """The bound is the width itself, not a slack multiple of it. The
    original `<= 40` for a 24-column dock passed while every row was
    width+1 — a gate loose enough to pass in both the healthy and the
    broken state, which is why the overflow survived to live use."""
    s = state(("x" * 200, "pending"))
    for line in as_text(render_plan_dock(s, C, width=24)).splitlines():
        assert cell_len(line) <= 24, repr(line)


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


# -- fitting the width -----------------------------------------------
#
# All three of these were found by rendering a real plan into a real pane
# rather than by reading the code. Each is a geometry assertion in cells,
# because `len()` is not width the moment a subject carries an emoji — the
# same East Asian Ambiguous problem the module docstring already warns
# about for the circles, one layer up.

def test_every_dock_row_fits_the_width_exactly():
    """Rows were width+1 at every width: the row is glyph + space + label
    + space + a 6-cell clock, so the label budget is width-9, and it was
    width-8."""
    s = state(("Reconcile the plan doc with what shipped", "completed"),
              ("Fix the task panel layout defects", "in_progress"),
              ("Task 13 — AGENTS.md, CHANGELOG, full suite", "pending"))
    for width in (24, 26, 36, 58):
        for line in as_text(render_plan_dock(s, C, width=width)).splitlines():
            assert cell_len(line) <= width, (width, repr(line))


def test_nested_subagent_rows_fit_the_width_too():
    """The indented rows carry the same budget arithmetic four columns in,
    so they overflowed identically and no test looked at them."""
    top = state(("dispatch", "in_progress"))
    sub = state(("Grep every consumer of the prefix", "completed"),
                ("Write the failing test for the dock", "in_progress"))
    out = as_text(render_plan_dock(top, C, width=36, subplans={"t1": sub}))
    for line in out.splitlines():
        assert cell_len(line) <= 36, repr(line)


def test_dock_measures_labels_in_cells_not_characters():
    """A subject with an emoji is one character but two cells wide, so
    padding by len() pushes that row's clock a column out of the column
    every other row lines up in."""
    s = state(("Deploy 🚀 the geocoder to demos and verify", "pending"),
              ("Plain ascii row of about the same length!", "pending"))
    rows = [l for l in as_text(render_plan_dock(s, C, width=36)).splitlines()
            if l.startswith(tuple(CIRCLES))]
    assert len({cell_len(r) for r in rows}) == 1, [repr(r) for r in rows]


def test_strip_fits_a_given_width_by_truncating_the_current_label():
    """The strip is documented as a one-line summary, but it took no width
    and never truncated, so a long active_form wrapped it to two lines and
    the transcript jumped every time the current task changed."""
    s = state(("Reconcile the plan doc", "completed"),
              ("Fix the task panel layout defects", "in_progress"),
              ("Docs and suite", "pending"),
              times={"Fix the task panel layout defects": 7.3})
    assert cell_len(as_text(render_plan_strip(s, C, width=40))) <= 40
    assert cell_len(as_text(render_plan_strip(s, C, width=80))) <= 80


def test_strip_without_a_width_is_unbounded():
    """Width is opt-in: the pure renderer stays pure for callers that are
    measuring rather than painting."""
    s = state(("a" * 300, "in_progress"))
    assert "a" * 300 in as_text(render_plan_strip(s, C))


def test_a_strip_too_narrow_for_a_label_still_shows_the_circles():
    """Degrade by dropping the label, never by dropping the progress —
    the circles and the count are the reason the strip exists."""
    s = state(("some quite long task subject here", "in_progress"),
              ("b", "pending"))
    out = as_text(render_plan_strip(s, C, width=18))
    assert cell_len(out) <= 18
    assert "0/2" in out


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
async def test_plan_strip_stays_one_line_in_a_narrow_pane():
    """The regression that started all this. Asserted on the laid-out
    widget, not the string: the pure renderer was fine on its own terms
    and it was `_paint` that never told it how much room there was."""
    from textual.app import App, ComposeResult
    from textual.containers import Vertical

    from aegis.tui.plan_strip import PlanStrip

    class _A(App):
        def compose(self) -> ComposeResult:
            with Vertical():
                yield PlanStrip(C, id="plan-strip")

    # The label must genuinely exceed the pane: the chrome is ~25 cells, so
    # at width 60 anything under ~35 characters fits and the assertion
    # passes without ever exercising the overflow. Present-continuous
    # `active_form` is the realistic worst case — it is what the strip
    # shows while a task is running, and it is the longer of the two.
    long = state(("Reconcile the plan doc", "completed"),
                 ("Fix the task panel layout defects", "in_progress"),
                 ("Docs and suite", "pending"))
    long = PlanState(tuple(
        t if t.status != "in_progress" else PlanTask(
            key=t.key, subject=t.subject, status=t.status,
            active_form="Fixing the task panel layout defects found in live use",
            working_s=7.3)
        for t in long.tasks))
    async with _A().run_test(size=(60, 10)) as pilot:
        strip = pilot.app.query_one("#plan-strip", PlanStrip)
        strip.refresh_plan(long, working=True)
        await pilot.pause()
        assert cell_len(as_text(render_plan_strip(long, C, working=True))) > 60, (
            "the fixture stopped overflowing — this test would pass vacuously")
        assert strip.size.height == 1, "the one-line strip wrapped"


@pytest.mark.asyncio
async def test_plan_dock_fills_its_content_box_exactly():
    """Neither overflowing nor leaving a dead column. `size` is already the
    content box, so subtracting the padding again wasted a column that the
    labels — truncated at p90 — could really use."""
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, VerticalScroll

    from aegis.tui.plan_dock import PlanDock

    class _A(App):
        def compose(self) -> ComposeResult:
            with Horizontal():
                yield VerticalScroll(id="transcript")
                yield PlanDock(C, id="plan-dock")

    s = state(("Reconcile the plan doc with what actually shipped", "completed"),
              ("Fix the task panel layout defects", "in_progress"))
    for term_w in (80, 120):
        async with _A().run_test(size=(term_w, 12)) as pilot:
            dock = pilot.app.query_one("#plan-dock", PlanDock)
            dock.toggle()
            dock.refresh_plan(s, {}, working=True)
            await pilot.pause()
            rows = [l for l in dock.render().plain.splitlines()
                    if l.startswith(tuple(CIRCLES))]
            assert rows
            for row in rows:
                assert cell_len(row) == dock.size.width, (
                    term_w, dock.size.width, repr(row))


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


@pytest.mark.asyncio
async def test_f3_toggles_the_dock_and_it_shows_the_session_plan():
    from aegis.events import AgentPlan, PlanEntry
    from aegis.tui.plan_dock import PlanDock

    async with _app().run_test() as pilot:
        pane = pilot.app._panes[0]
        dock = pane.query_one("#plan-dock", PlanDock)
        pane._core._fire_event(AgentPlan(entries=(
            PlanEntry(content="explore", status="completed"),
            PlanEntry(content="build", status="in_progress"),
        )))
        await pilot.pause()
        assert dock.display is False           # hidden until asked for

        await pilot.press("f3")
        await pilot.pause()
        assert dock.display is True
        out = dock.render().plain
        assert "explore" in out and "build" in out and "1/2" in out

        await pilot.press("f3")
        await pilot.pause()
        assert dock.display is False


@pytest.mark.asyncio
async def test_the_tasks_command_toggles_the_same_dock():
    """One dispatch seam, so the web client gets the toggle for free."""
    from aegis.tui.plan_dock import PlanDock

    async with _app().run_test() as pilot:
        pane = pilot.app._panes[0]
        dock = pane.query_one("#plan-dock", PlanDock)
        pane._apply_command_effect({"kind": "tasks"})
        await pilot.pause()
        assert dock.display is True


@pytest.mark.asyncio
async def test_the_transcript_keeps_its_width_while_the_dock_is_shut():
    """A hidden dock must cost nothing — the transcript owns the row."""
    from aegis.tui.plan_dock import PlanDock

    async with _app().run_test(size=(120, 30)) as pilot:
        pane = pilot.app._panes[0]
        wide = pane.query_one("#transcript").size.width
        pane.query_one("#plan-dock", PlanDock).toggle()
        await pilot.pause()
        narrow = pane.query_one("#transcript").size.width
        assert narrow < wide, "opening the dock must reflow the transcript"
        assert narrow > 40, "the transcript must keep a usable width"


@pytest.mark.asyncio
async def test_dock_labels_use_the_dock_s_real_width_not_the_minimum():
    """A wider terminal must mean longer labels. toggle() paints before
    Textual lays out a previously-hidden widget, so without an on_resize
    repaint the dock renders at DOCK_MIN and a 200-col terminal shows
    SHORTER labels than a 120-col one."""
    from aegis.events import AgentPlan, PlanEntry
    from aegis.tui.plan_dock import PlanDock

    subject = "Explore the plan-rendering context in some detail"

    async def widest_label(cols):
        async with _app().run_test(size=(cols, 30)) as pilot:
            pane = pilot.app._panes[0]
            pane._core._fire_event(AgentPlan(entries=(
                PlanEntry(content=subject, status="in_progress"),)))
            await pilot.pause()
            await pilot.press("f3")
            await pilot.pause()
            dock = pane.query_one("#plan-dock", PlanDock)
            row = dock.render().plain.splitlines()[1]
            return dock.size.width, len(row)

    narrow_w, narrow_label = await widest_label(100)
    wide_w, wide_label = await widest_label(200)
    assert wide_w > narrow_w
    assert wide_label > narrow_label, (
        f"dock {wide_w} cols rendered a {wide_label}-char row while "
        f"dock {narrow_w} cols rendered {narrow_label}")
