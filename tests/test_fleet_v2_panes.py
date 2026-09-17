from aegis.fleet.models import CardView, EventLine, MonitorRow, Origin
from aegis.fleet.render import render_detail, render_item
from aegis.plan.models import PlanTask
from aegis.tui.themes import INK, aegis_colors

P = aegis_colors(INK)
LONG = (
    "Fixed the F10 grid: a newline inside a Bash summary split one card row across two "
    "terminal lines; every session text is now sanitized before drawing, with a failing test first."
)


def _card(**kw):
    base = dict(
        handle="fleet-dashboard-f10",
        title="Fleet dashboard F10",
        agent_slug="opus",
        repo="aegis · main +2 ~5",
        state="ready",
        did=LONG,
        ctx_pct=92.0,
        ctx_tokens=920_000,
        ctx_window=1_000_000,
        uptime_s=68_400,
        tab_index=1,
        cost_usd=168.02,
    )
    base.update(kw)
    return CardView(**base)


def test_an_item_is_at_least_three_lines_and_keeps_did_whole():
    text = render_item(_card(), P, 0).plain
    lines = text.split("\n")
    assert len(lines) >= 3
    assert LONG in text.replace("\n", " ")
    assert "…" not in text


def test_a_working_item_shows_now_instead_of_did():
    text = render_item(_card(state="working", doing="Running the suite."), P, 0).plain
    assert "now Running the suite." in text and "did " not in text


def test_an_item_with_no_recap_falls_back_to_commands():
    ev = EventLine(at=0.0, tool="Bash", summary="uv run pytest")
    text = render_item(_card(did="", events=(ev,)), P, 0).plain
    assert "uv run pytest" in text


def test_a_pending_needs_input_leads_the_item_and_blinks():
    on = render_item(_card(attention="needs_input"), P, 0).plain
    off = render_item(_card(attention="needs_input"), P, 1).plain
    assert "?" in on.split("\n")[0] and "?" not in off.split("\n")[0]
    assert "needs you" in on.split("\n")[0]


def test_an_ephemeral_item_names_its_origin():
    card = _card(origin=Origin(kind="queue", by="tasks", returns_to="une-tools-tasks"))
    assert "tasks" in render_item(card, P, 0).plain.split("\n")[1]


def test_the_detail_orders_its_sections():
    card = _card(
        state="working",
        doing="Running the suite.",
        monitors=(MonitorRow("m1", "pytest -n auto", 60.0, 32.0, 48.0),),
        plan_done=3,
        plan_total=5,
        plan_tasks=(PlanTask(key="1", subject="Write the spec", status="completed"),),
        events=(EventLine(at=0.0, tool="Edit", summary="render.py"),),
    )
    text = render_detail(card, P, 90, 0).plain
    order = ["NOW", "DID", "MONITORS", "GAUGES", "PLAN", "ACTIVITY", "SPEND"]
    positions = [text.index(h) for h in order]
    assert positions == sorted(positions)
    assert "ETA 32s" in text and "60%" in text


def test_an_indeterminate_monitor_sweeps_and_says_no_eta():
    card = _card(monitors=(MonitorRow("m1", "release CI", None, None, 190.0),))
    a = render_detail(card, P, 90, 0).plain
    b = render_detail(card, P, 90, 1).plain
    assert "no ETA" in a and a != b


def test_an_empty_section_is_omitted():
    text = render_detail(
        _card(doing="", monitors=(), plan_tasks=(), events=()), P, 90, 0
    ).plain
    assert "NOW" not in text and "MONITORS" not in text and "PLAN" not in text
