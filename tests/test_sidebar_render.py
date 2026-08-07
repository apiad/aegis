"""The pure sidebar renderer.

Sections are ordered by volatility, highest first: on a short terminal the
panel scrolls, and what you see without scrolling should be what moves.
An empty section renders nothing at all — not a heading over a blank.
"""
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
