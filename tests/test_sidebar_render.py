"""The pure sidebar renderer.

Sections are ordered by volatility, highest first: on a short terminal the
panel scrolls, and what you see without scrolling should be what moves.
An empty section renders nothing at all — not a heading over a blank.
"""
from rich.cells import cell_len

from aegis.monitor.schema import MonitorView
from aegis.plan import PlanState, PlanTask
from aegis.queue.digest import QueueView, Snapshot
from aegis.tui.sidebar import SidebarModel, heading, render_sidebar
from aegis.tui.themes import INK, aegis_colors

C = aegis_colors(INK)          # house pattern — see tests/test_render_event.py


def as_text(renderable) -> str:
    return renderable.plain


def test_an_empty_model_renders_nothing():
    assert as_text(render_sidebar(SidebarModel(), C, 40)) == ""


def test_session_section_renders_title_identity_and_state():
    m = SidebarModel(title="fix the eviction race",
                     identity=("opus · high · local",),
                     state_label="✻ working…")
    out = as_text(render_sidebar(m, C, 40))
    assert "SESSION" in out
    assert "fix the eviction race" in out
    assert "opus · high · local" in out
    assert "✻ working…" in out


def test_a_section_with_no_content_omits_its_heading():
    """CONTEXT has no metrics and no quota, so the word never appears."""
    m = SidebarModel(state_label="idle")
    out = as_text(render_sidebar(m, C, 40))
    assert "SESSION" in out
    assert "CONTEXT" not in out


def test_sections_are_separated_by_one_blank_row():
    m = SidebarModel(state_label="idle", metrics=("$1.84",))
    lines = as_text(render_sidebar(m, C, 40)).split("\n")
    assert "" in lines
    assert lines.count("") == 1


def test_connection_warning_leads_the_session_section():
    """A disconnected session is a fact about the session, and burying it
    under its own heading at some scroll offset would be worse than the
    status bar it replaces."""
    m = SidebarModel(state_label="idle",
                     connection=("⚠ disconnected — reconnecting…",
                                 "⚠ disconnected"))
    lines = [ln for ln in as_text(render_sidebar(m, C, 40)).split("\n") if ln]
    assert lines[0] == "SESSION"
    assert lines[1].startswith("⚠ disconnected")


def test_a_narrow_column_takes_a_narrower_tier():
    m = SidebarModel(connection=("⚠ disconnected — reconnecting…",
                                 "⚠ disconnected"),
                     state_label="idle")
    assert "⚠ disconnected — reconnecting…" in as_text(
        render_sidebar(m, C, 40))
    assert "⚠ disconnected — reconnecting…" not in as_text(
        render_sidebar(m, C, 20))
    assert "⚠ disconnected" in as_text(render_sidebar(m, C, 20))


def test_heading_right_aligns_its_counter():
    assert as_text(heading("PLAN", C, 20, right="3/7")) == \
        "PLAN             3/7"


def test_heading_without_a_counter_is_just_the_word():
    assert as_text(heading("SESSION", C, 20)) == "SESSION"


# --- the four sections that reuse an existing renderer ------------------


def _plan():
    # PlanTask is the *tracker* model — key/subject/status — and `tasks` is
    # a tuple because PlanState is frozen. Not PlanEntry, which is the
    # parsed event shape and does use `content`.
    return PlanState(tasks=(
        PlanTask(key="1", subject="parse the header", status="completed"),
        PlanTask(key="2", subject="writing the parser", status="in_progress"),
        PlanTask(key="3", subject="wire the strip", status="pending"),
    ))


def test_plan_section_shows_the_count_in_its_heading():
    out = as_text(render_sidebar(SidebarModel(plan=_plan()), C, 40))
    assert "PLAN" in out
    assert "1/3" in out


def test_plan_section_lists_the_tasks():
    out = as_text(render_sidebar(SidebarModel(plan=_plan()), C, 40))
    assert "writing the parser" in out


def test_queues_section_lists_each_queue():
    snap = Snapshot(queues=[
        QueueView(name="build", agent="opus", max_parallel=2,
                  running=1, queued=3, ok=5, err=0),
        QueueView(name="review", agent="opus", max_parallel=1,
                  running=0, queued=0, ok=0, err=0),
    ])
    out = as_text(render_sidebar(SidebarModel(queues=snap), C, 40))
    assert "QUEUES" in out
    assert "build" in out and "review" in out
    assert "●1" in out


def test_monitors_section_shows_the_bar():
    v = MonitorView(id="m1", description="pytest", state="running",
                    pct=62.0, eta_s=100.0, elapsed_s=30.0)
    out = as_text(render_sidebar(SidebarModel(monitors=[v]), C, 40))
    assert "MONITORS" in out
    assert "pytest" in out
    assert "62%" in out


def test_system_section_is_last():
    m = SidebarModel(state_label="idle", system=("cpu 34% ram 61%",))
    lines = [ln for ln in as_text(render_sidebar(m, C, 40)).split("\n") if ln]
    assert lines[0] == "SESSION"
    assert "SYSTEM" in lines
    assert lines.index("SYSTEM") == len(lines) - 2


def test_system_section_carries_clock_cwd_and_build_under_the_meters():
    """Volatility again, one level down: the meters move every tick, the
    clock every minute, and the last two never move at all."""
    m = SidebarModel(system=("CPU 34% · RAM 61% · DSK 12%",),
                     clock=("2026-08-11 11:03 CDT · en_US.UTF-8",),
                     cwd=("CWD ~/Workspace/repos/aegis",),
                     build=("aegis 0.21.0+d35b07a",))
    lines = [ln for ln in as_text(render_sidebar(m, C, 60)).split("\n") if ln]
    assert lines == ["SYSTEM",
                     "CPU 34% · RAM 61% · DSK 12%",
                     "2026-08-11 11:03 CDT · en_US.UTF-8",
                     "CWD ~/Workspace/repos/aegis",
                     "aegis 0.21.0+d35b07a"]


def test_system_section_renders_without_the_meters():
    """psutil is sampled inside a suppress() — the static rows must not
    disappear with it."""
    out = as_text(render_sidebar(SidebarModel(cwd=("CWD ~/w",)), C, 40))
    assert "SYSTEM" in out
    assert "CWD ~/w" in out


def test_full_model_renders_every_section_in_volatility_order():
    m = SidebarModel(
        title="fix the eviction race", identity=("opus · high",),
        state_label="✻ working…", metrics=("$1.84",), plan=_plan(),
        queues=Snapshot(queues=[QueueView(
            name="build", agent="opus", max_parallel=2,
            running=1, queued=0, ok=0, err=0)]),
        monitors=[MonitorView(id="m1", description="pytest", state="running",
                              pct=62.0, eta_s=None, elapsed_s=30.0)],
        system=("cpu 34%",))
    lines = [ln for ln in as_text(render_sidebar(m, C, 40)).split("\n") if ln]
    heads = [ln.split()[0] for ln in lines
             if ln.split() and ln.split()[0].isupper()]
    assert heads == ["SESSION", "CONTEXT", "PLAN", "QUEUES",
                     "MONITORS", "SYSTEM"]


# -- fitting the width -------------------------------------------------
#
# The column is 26..60 cells wide and its body is a Static inside a
# VerticalScroll, so an over-long row does not clip — it *wraps*, and one
# monitor silently becomes three rows that push the sections below it off
# screen. Every other surface in this file's neighbourhood already pays
# for this lesson; the sidebar's own renderer had no such assertion.


def _every_row_fits(m: SidebarModel, width: int) -> None:
    for line in as_text(render_sidebar(m, C, width)).split("\n"):
        assert cell_len(line) <= width, (width, repr(line))


def test_a_long_queue_name_does_not_overflow_the_column():
    snap = Snapshot(queues=[QueueView(
        name="documentation-backfill-workers", agent="opus",
        max_parallel=4, running=3, queued=17, ok=128, err=6)])
    for width in (26, 33, 40, 60):
        _every_row_fits(SidebarModel(queues=snap), width)


def test_a_long_monitor_description_does_not_overflow_the_column():
    """`format_mon` was written for a full-width strip, where the bar sits
    far to the right of any realistic description. In a 26-cell column the
    description alone can be wider than the row."""
    mons = [MonitorView(id="m1", state="running", elapsed_s=154.0,
                        description="the full hermetic suite plus the live "
                                    "round-trips", pct=42.0, eta_s=930.0),
            MonitorView(id="m2", state="running", elapsed_s=12.0,
                        description="rebuilding the airgapped .deb bundle",
                        pct=None, eta_s=None)]
    for width in (26, 33, 40, 60):
        _every_row_fits(SidebarModel(monitors=mons), width)


def test_the_counters_survive_a_name_that_has_to_be_cut():
    """Truncation comes out of the variable half. A queue whose counters
    were cut instead would show a name and no numbers, which is the half
    with no information in it."""
    snap = Snapshot(queues=[QueueView(
        name="documentation-backfill-workers", agent="opus",
        max_parallel=4, running=3, queued=17, ok=128, err=6)])
    out = as_text(render_sidebar(SidebarModel(queues=snap), C, 26))
    assert "●3/4" in out and "○17" in out and "✓128" in out and "✗6" in out
    assert "…" in out


def test_the_monitor_tail_yields_before_the_description_does():
    """Both halves give way, in this order. A row cut only from the right
    would keep a description already legible at half the width and throw
    away the bar, the percentage and the ETA. A row that only cut the
    description would leave "the full h…", which does not say which of
    three monitors it is."""
    v = MonitorView(id="m1", state="running", elapsed_s=154.0,
                    description="the full hermetic suite plus the live ones",
                    pct=42.0, eta_s=930.0)

    wide = as_text(render_sidebar(SidebarModel(monitors=[v]), C, 60))
    assert "▓" in wide and "42%" in wide and "ETA 15:30" in wide

    narrow = as_text(render_sidebar(SidebarModel(monitors=[v]), C, 26))
    assert "▓" not in narrow          # the bar goes first
    assert "42%" in narrow            # the number is the last thing kept
    assert "…" in narrow              # and only then is the label cut
    row = narrow.split("\n")[-1]
    assert cell_len(row.split("…")[0]) >= 14, row


def test_the_plan_trim_drops_only_the_docks_own_header():
    """The PLAN section drops `render_plan_dock`'s first line, which is
    the dock's `tasks d/t` header. A subagent's `└ subagent d/t` header is
    a *middle* line and must survive — losing it would leave the nested
    rows dangling under the top-level plan with nothing saying whose they
    are, which is the whole point of nesting them."""
    top = PlanState(tasks=(PlanTask(key="1", subject="dispatch",
                                    status="in_progress"),))
    sub = PlanState(tasks=(
        PlanTask(key="a", subject="grind", status="in_progress"),
        PlanTask(key="b", subject="finished", status="completed")))
    out = as_text(render_sidebar(
        SidebarModel(plan=top, subplans={"tool_1": sub}), C, 40))
    assert "tasks 0/1" not in out          # the dock header is gone
    assert "PLAN" in out and "0/1" in out  # ...and the section carries it
    assert "subagent 1/2" in out           # the nested header survives
    assert "dispatch" in out and "grind" in out and "finished" in out


def test_nested_rows_line_up_on_the_same_right_edge():
    """The four indent columns come out of the label, so a subagent row
    ends where a top-level row ends. A section that re-budgeted the width
    would break the column the eye tracks."""
    top = PlanState(tasks=(PlanTask(key="1", subject="dispatch a fan-out",
                                    status="in_progress", working_s=61.0),))
    sub = PlanState(tasks=(PlanTask(key="a", subject="grind on it",
                                    status="in_progress", working_s=42.0),))
    lines = [ln for ln in as_text(render_sidebar(
        SidebarModel(plan=top, subplans={"tool_1": sub}), C, 40)).split("\n")
        if ln.rstrip().endswith(("1:01", "0:42"))]
    assert len(lines) == 2, lines
    assert len({cell_len(ln) for ln in lines}) == 1, lines


def test_a_coloured_metrics_segment_survives_the_rich_parser():
    """The CONTEXT rows come from `SessionMetrics.render_tiers`, which is
    also read by the StatusBar — a Textual `Static`. The sidebar parses the
    same strings with *Rich*, and the two markup dialects are not the same
    one: Textual accepts `[$error]`, Rich reads the closing `[/$error]` as
    a stray tag and raises `MarkupError`. The colours only appear once the
    context is half full, so the crash waits for a long session to arrive.
    """
    from aegis.tui.metrics import SessionMetrics
    hot = SessionMetrics(context_window=200_000, last_true_input=160_000,
                         compaction_count=2)
    out = as_text(render_sidebar(
        SidebarModel(metrics=tuple(hot.render_tiers(now=0.0, colors=C))),
        C, 60))
    assert "CONTEXT" in out
    assert "ctx 160k (80%)" in out
    assert "✂2" in out
    assert "$error" not in out and "[" not in out
