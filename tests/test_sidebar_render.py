"""The pure sidebar renderer.

Sections are ordered by volatility, highest first: on a short terminal the
panel scrolls, and what you see without scrolling should be what moves.
An empty section renders nothing at all — not a heading over a blank.
"""
from rich.cells import cell_len

from aegis.fleet.models import QuotaGauge
from aegis.fleet.render import ctx_style
from aegis.monitor.schema import MonitorView
from aegis.plan import PlanState, PlanTask
from aegis.queue.digest import QueueView, Snapshot
from aegis.tui.sidebar import SidebarModel, heading, heading_fits, render_sidebar
from aegis.tui.metrics import ContextGauge
from aegis.tui.sysmeter import SystemStats
from aegis.tui.themes import INK, aegis_colors

C = aegis_colors(INK)          # house pattern — see tests/test_render_event.py


def as_text(renderable) -> str:
    return renderable.plain


def heads(rendered: str) -> list[str]:
    """The section names, read off the rule headings. A heading is a rule
    now, not a bare word, so `ln.isupper()` no longer finds one."""
    return [ln.split()[1] for ln in rendered.split("\n") if ln.startswith("── ")]


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


def test_sections_are_separated_by_the_rule_and_nothing_else():
    m = SidebarModel(state_label="idle", metrics=("$1.84",))
    lines = as_text(render_sidebar(m, C, 40)).split("\n")
    assert "" not in lines, "a blank row survived between two sections"
    assert sum(1 for ln in lines if ln.startswith("── ")) == 2


def test_connection_warning_leads_the_session_section():
    """A disconnected session is a fact about the session, and burying it
    under its own heading at some scroll offset would be worse than the
    status bar it replaces."""
    m = SidebarModel(state_label="idle",
                     connection=("⚠ disconnected — reconnecting…",
                                 "⚠ disconnected"))
    lines = [ln for ln in as_text(render_sidebar(m, C, 40)).split("\n") if ln]
    assert lines[0].startswith("── SESSION")
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


def test_a_heading_is_a_rule_that_fills_its_width():
    assert as_text(heading("PLAN", C, 20)) == "── PLAN ────────────"


def test_a_heading_with_a_counter_puts_it_at_the_far_end():
    assert as_text(heading("PLAN", C, 20, right="3/7")) == "── PLAN ──────── 3/7"


def test_a_heading_drops_a_counter_it_cannot_seat():
    """Below RULE_FLOOR rule cells the heading would read as a word, a gap
    and a label with nothing joining them — the shape this change removes.
    The counter is dropped and the rule stays whole."""
    assert not heading_fits("MONITORS", 18, "✻ working… · ◐ thinking")
    assert as_text(heading("MONITORS", C, 18, right="✻ working… · ◐ thinking")) == \
        "── MONITORS ──────"


def test_a_heading_measures_in_cells_not_characters():
    """A wide counter must not push the rule past the column.

    The input is synthetic, and deliberately so: every glyph aegis puts in
    this slot today (✻ ◐ ⚠ ✓ ✗ ◆ ⧗) is East Asian Ambiguous, which Rich
    measures as one cell — so `len` and `cell_len` agree on all of them and
    no real value can tell the two apart. `right` is not aegis's to choose
    forever, though: it carries a state label, and a harness that returns a
    wide one would overflow the column silently, because Textual clips
    without saying so. A CJK counter is the smallest input that fails if
    this function ever goes back to counting characters.
    """
    assert cell_len(as_text(heading("REPOS", C, 30, right="項目 2"))) == 30


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
    out = as_text(render_sidebar(m, C, 40))
    lines = [ln for ln in out.split("\n") if ln]
    assert heads(out) == ["SESSION", "SYSTEM"]
    assert [i for i, ln in enumerate(lines)
            if ln.startswith("── SYSTEM")] == [len(lines) - 2]


def test_system_section_carries_clock_cwd_and_build_under_the_meters():
    """Volatility again, one level down: the meters move every tick, the
    clock every minute, and the last two never move at all."""
    m = SidebarModel(system=("CPU 34% · RAM 61% · DSK 12%",),
                     clock=("2026-08-11 11:03 CDT · en_US.UTF-8",),
                     cwd=("CWD ~/Workspace/repos/aegis",),
                     build=("aegis 0.21.0+d35b07a",))
    lines = [ln for ln in as_text(render_sidebar(m, C, 60)).split("\n") if ln]
    assert lines[0].startswith("── SYSTEM")
    assert lines[1:] == ["CPU 34% · RAM 61% · DSK 12%",
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
    assert heads(as_text(render_sidebar(m, C, 40))) == [
        "SESSION", "CONTEXT", "PLAN", "QUEUES", "MONITORS", "SYSTEM"]


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
    assert "█" in wide and "42%" in wide and "ETA 15:30" in wide

    narrow = as_text(render_sidebar(SidebarModel(monitors=[v]), C, 26))
    assert "█" not in narrow          # the bar goes first
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


def test_the_now_line_rides_in_the_session_section():
    m = SidebarModel(title="t", now_line="wiring the fleet watcher")
    out = as_text(render_sidebar(m, C, 40))
    assert "SESSION" in out
    # The label is padded to the gauge label column, so the recap lines up
    # with CTX/QUOTA/LOOP rather than sitting two cells to their left.
    assert "now   wiring the fleet watcher" in out


def test_no_recap_draws_no_now_line():
    out = as_text(render_sidebar(SidebarModel(title="t"), C, 40))
    assert "now" not in out


# --- SESSION: the heading slot, the recap fold, the loop gauge ----------


def test_the_state_label_rides_the_session_heading():
    m = SidebarModel(title="fix the eviction race", state_label="✻ working")
    lines = as_text(render_sidebar(m, C, 56)).split("\n")
    assert lines[0].startswith("── SESSION")
    assert lines[0].endswith("✻ working")
    assert "✻ working" not in lines[1]


def test_a_state_label_too_long_for_the_heading_keeps_its_own_row():
    m = SidebarModel(title="t", state_label="✻ working… · ◐ thinking")
    lines = [ln for ln in as_text(render_sidebar(m, C, 26)).split("\n") if ln]
    assert "✻ working… · ◐ thinking" not in lines[0]
    assert "✻ working… · ◐ thinking" in lines


def test_the_recap_continuation_is_indented_under_its_label():
    m = SidebarModel(state_label="idle",
                     now_line="reading pane.py to find where the recap lands")
    rows = [ln for ln in as_text(render_sidebar(m, C, 26)).split("\n")
            if ln and not ln.startswith("── ")]
    tail = [r for r in rows if r.startswith("      ")]
    assert tail, "the recap wrapped flush left"
    assert all(cell_len(r) <= 26 for r in rows)


def test_the_loop_draws_a_bar():
    m = SidebarModel(state_label="idle",
                     loop_status={"iteration": 3, "max_iterations": 20})
    out = as_text(render_sidebar(m, C, 56))
    assert "LOOP" in out and "█" in out and "3/20" in out


def test_a_loop_without_a_status_falls_back_to_its_tier():
    """A remote pane gets the rendered string and no dict."""
    m = SidebarModel(state_label="idle", loop=("⟳ loop 3/20",))
    assert "⟳ loop 3/20" in as_text(render_sidebar(m, C, 56))


# --- CONTEXT: the window and the quota as bars --------------------------


def test_context_draws_a_bar_for_the_window():
    m = SidebarModel(ctx=ContextGauge(pct=71, live=142_000, window=200_000))
    out = as_text(render_sidebar(m, C, 56))
    assert "CTX" in out and "█" in out and "71%" in out
    assert "142k/200k" in out


def test_the_context_bar_takes_its_colour_from_the_pressure():
    """Asserted on `ctx_style` rather than on Rich spans: the behaviour under
    test is that pressure picks the colour, and reaching into a Text's span
    list couples the test to Rich internals instead."""
    assert ctx_style(95, C) == C.error
    assert ctx_style(65, C) == C.accent
    assert ctx_style(20, C) == C.ready


def test_quota_draws_one_bar_per_window_with_its_reset():
    m = SidebarModel(quota_gauges=(
        QuotaGauge(label="cc 5h", percent=47.0, severity="normal",
                   resets_in_s=11040),
    ))
    out = as_text(render_sidebar(m, C, 56))
    assert "cc 5h" in out and "47%" in out and "↻ 3h04m" in out


def test_no_quota_reading_falls_back_to_the_tier_not_to_a_zero_bar():
    """No credentials configured. A 0% bar would claim a reading of zero
    rather than no reading at all."""
    m = SidebarModel(quota=("quota unavailable",), quota_gauges=())
    out = as_text(render_sidebar(m, C, 56))
    assert "quota unavailable" in out
    assert "0%" not in out


def test_no_context_window_falls_back_to_the_metrics_tier():
    m = SidebarModel(ctx=None, metrics=("↑142k ↓8.2k · $1.84 · 1:20",))
    out = as_text(render_sidebar(m, C, 56))
    assert "$1.84" in out
    assert "CTX" not in out


# --- SYSTEM: three meters as bars, the two static rows merged ----------


def _sys_rows(m, width):
    return [ln for ln in as_text(render_sidebar(m, C, width)).split("\n")
            if ln and not ln.startswith("── ")]


def test_system_draws_three_meters_as_bars_on_two_rows():
    m = SidebarModel(stats=SystemStats(cpu=34.0, ram=61.0, disk=82.0))
    rows = _sys_rows(m, 56)
    assert len(rows) == 2
    assert "CPU" in rows[0] and "RAM" in rows[0]
    assert "DSK" in rows[1]
    assert "█" in rows[0]


def test_a_narrow_column_puts_one_meter_per_row():
    m = SidebarModel(stats=SystemStats(cpu=34.0, ram=61.0, disk=82.0))
    assert len(_sys_rows(m, 26)) == 3


def test_cwd_and_build_survive_a_column_too_narrow_for_both_on_one_row():
    """Merging them onto one row was tried and reverted. `fit_rows` drops a
    segment whose narrowest tier overflows rather than truncating it, so a
    merged row takes BOTH answers down together on a narrow column — and
    these two are the questions a stale checkout makes you ask, which is the
    worst pair to lose. Separate rows cost one row and lose nothing.
    """
    m = SidebarModel(cwd=("CWD /tmp/pytest-of-apiad/test_a_very_long_name_0",
                          "CWD test_a_very_long_name_0"),
                     build=("aegis 0.38.0+abc1234", "0.38.0"))
    rows = _sys_rows(m, 36)
    assert any("test_a_very_long_name_0" in r for r in rows)
    assert any("0.38.0" in r for r in rows)


def test_no_stats_falls_back_to_the_system_tier():
    m = SidebarModel(stats=None, system=("cpu 34% ram 61% disk 82%",))
    assert "cpu 34%" in as_text(render_sidebar(m, C, 56))


# --- QUEUES: saturation as a bar ---------------------------------------


def test_a_queue_row_leads_with_its_saturation():
    m = SidebarModel(queues=Snapshot(queues=(
        QueueView(name="build", agent="claude", running=1, max_parallel=2,
                  queued=3, ok=5, err=0),
    )))
    row = [ln for ln in as_text(render_sidebar(m, C, 56)).split("\n")
           if "build" in ln][0]
    assert "█" in row and "●1/2" in row
    assert cell_len(row) <= 56


def test_a_queue_with_no_parallelism_configured_does_not_divide_by_zero():
    m = SidebarModel(queues=Snapshot(queues=(
        QueueView(name="idle", agent="claude", running=0, max_parallel=0,
                  queued=0, ok=0, err=0),
    )))
    assert "idle" in as_text(render_sidebar(m, C, 56))


def test_a_long_plan_does_not_evict_the_sections_below_it():
    tasks = tuple(PlanTask(key=str(i), subject=f"task {i}",
                           status="in_progress" if i == 10 else "pending")
                  for i in range(20))
    m = SidebarModel(plan=PlanState(tasks=tasks),
                     system=("cpu 34% ram 61% disk 82%",))
    out = as_text(render_sidebar(m, C, 56))
    assert "SYSTEM" in out
    assert "+16 more" in out
    assert "task 10" in out
    assert "task 0" not in out
