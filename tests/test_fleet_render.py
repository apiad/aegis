"""The pure card renderer. Chrome is English; content is the session's own."""

from aegis.fleet.models import (
    BandView,
    CardView,
    EventLine,
    FleetSnapshot,
    Origin,
    RepoCount,
)
from aegis.fleet.render import columns_for, render_card, render_fleet
from aegis.tui.themes import INK, aegis_colors

C = aegis_colors(INK)
W = 42


def as_text(renderable) -> str:
    return renderable.plain


def test_a_card_leads_with_the_handle_and_the_title():
    out = as_text(
        render_card(CardView(handle="une-tools-tasks", title="ordenar tareas"), C, W)
    )
    assert "une-tools-tasks" in out
    assert "ordenar tareas" in out


def test_the_plan_renders_as_a_bar_with_its_counts():
    out = as_text(render_card(CardView(handle="a", plan_done=7, plan_total=10), C, W))
    assert "7/10" in out


def test_a_session_with_no_plan_draws_no_plan_row():
    out = as_text(render_card(CardView(handle="a"), C, W))
    assert "plan" not in out


def test_the_two_recap_lines_are_labelled_in_english():
    out = as_text(
        render_card(
            CardView(handle="a", did="3 new tests", doing="closing the loop"), C, W
        )
    )
    assert "did" in out and "3 new tests" in out
    assert "now" in out and "closing the loop" in out


def test_an_empty_doing_line_is_omitted_not_blank():
    out = as_text(render_card(CardView(handle="a", did="landed x"), C, W))
    assert "did" in out
    assert "now" not in out


def test_an_ephemeral_card_leads_with_its_origin_and_destination():
    card = CardView(
        handle="brisk-babbage",
        origin=Origin(
            kind="queue", by="general", detail="a3f2", returns_to="rosy-rivest"
        ),
    )
    out = as_text(render_card(card, C, W))
    assert "queue general #a3f2" in out
    assert "rosy-rivest" in out


def test_no_row_exceeds_the_width():
    """Textual clips an over-long line silently — the reason aegis.tui.fit
    exists. A card that overflows corrupts the whole grid's columns."""
    from rich.cells import cell_len

    card = CardView(
        handle="a-very-long-handle-indeed",
        title="un titulo larguisimo que no cabe de ninguna manera",
        did="x" * 200,
        doing="y" * 200,
        events=tuple(EventLine(at=0, tool="Bash", summary="z" * 120) for _ in range(5)),
    )
    for row in as_text(render_card(card, C, W)).split("\n"):
        assert cell_len(row) <= W, f"row overflows: {row!r}"


def test_an_empty_fleet_says_so_rather_than_drawing_nothing():
    out = as_text(render_fleet(FleetSnapshot(), C, 120))
    assert "no sessions" in out.lower()


def test_columns_follow_the_width():
    assert columns_for(40) == 1
    assert columns_for(120) == 2
    assert columns_for(200) == 4


def test_a_narrow_terminal_still_gets_one_column():
    """Never zero: `width // 50` is 0 below 50 cells and would divide by it."""
    assert columns_for(10) == 1


def test_the_band_names_the_mix():
    band = BandView(
        host="zion",
        total=9,
        yours=6,
        ephemeral=3,
        by_kind=(("queue", 2), ("workflow", 1)),
    )
    out = as_text(render_fleet(FleetSnapshot(band=band), C, 160))
    assert "9 agents" in out
    assert "6 yours" in out
    assert "3 ephemeral" in out
    assert "2 queue" in out


def test_a_shared_repo_is_marked():
    """Two agents in one working tree is the condition that costs an
    afternoon, and no other surface in aegis shows it."""
    band = BandView(
        repos=(
            RepoCount(name="une-tools", agents=2, shared=True),
            RepoCount(name="aegis", agents=1),
        )
    )
    out = as_text(render_fleet(FleetSnapshot(band=band), C, 160))
    assert "une-tools ×2" in out
    assert "⚠" in out


def test_the_recap_spend_rides_in_the_band():
    """A paid call whose bill is not on screen is a paid call nobody audits."""
    out = as_text(
        render_fleet(
            FleetSnapshot(band=BandView(recap_cost=1.20, recap_calls=340)), C, 160
        )
    )
    assert "1.20" in out
    assert "340" in out


def test_the_grid_never_exceeds_the_terminal_width():
    from rich.cells import cell_len

    cards = tuple(CardView(handle=f"session-{i}", title="x" * 60) for i in range(9))
    out = as_text(render_fleet(FleetSnapshot(cards=cards), C, 160))
    for row in out.split("\n"):
        assert cell_len(row) <= 160


def test_a_working_card_shows_its_uptime_in_the_footer():
    """The cap carries the turn; the footer always leads with the uptime, so
    a three-hour session mid-turn does not look newer than a fresh one."""
    old = CardView(handle="a", state="working", turn_s=45, uptime_s=11000)
    new = CardView(handle="a", state="working", turn_s=45, uptime_s=120)
    old_out = as_text(render_card(old, C, W))
    assert old_out != as_text(render_card(new, C, W))
    assert "3h03m" in old_out
    cap = old_out.split("\n")[0]
    assert "45s" in cap and "3h03m" not in cap


def test_an_idle_card_cap_carries_no_age():
    out = as_text(render_card(CardView(handle="a", uptime_s=11000), C, W))
    cap, *rest = out.split("\n")
    assert "3h03m" not in cap and "●" in cap
    assert any(line.lstrip("│ ").startswith("3h03m") for line in rest)


def test_the_footer_draws_both_conversation_edges():
    card = CardView(handle="a", spoke_with=("une-demo-prep", "x"), waiting_on=("rosy",))
    out = as_text(render_card(card, C, 60))
    assert "← une-demo-prep" in out
    assert "→ rosy" in out
    assert "waiting on" not in out


def _grid_row():
    return (
        CardView(
            handle="a",
            title="t",
            did="d",
            doing="n",
            tab_index=1,
            events=(EventLine(at=0, tool="Bash", summary="x"),),
        ),
        CardView(handle="b", tab_index=2),
        CardView(handle="c", title="t", ctx_pct=10, tab_index=3),
    )


def test_cards_in_a_grid_row_share_a_bottom_border():
    width = 3 * 48
    out = as_text(render_fleet(FleetSnapshot(cards=_grid_row()), C, width))
    lines = out.split("\n")
    bottoms = [
        {i for i, line in enumerate(lines) if line[x : x + 1] == "└"}
        for x in (0, 48, 96)
    ]
    assert bottoms[0] and bottoms[0] == bottoms[1] == bottoms[2]


def test_no_grid_line_ends_in_a_space():
    out = as_text(render_fleet(FleetSnapshot(cards=_grid_row()[:2]), C, 3 * 48))
    assert all(not line.endswith(" ") for line in out.split("\n"))


def test_a_ghost_reads_closed_with_its_age():
    card = CardView(
        handle="w9", origin=Origin(kind="queue"), ghost_since=1.0, ghost_s=23
    )
    assert "closed 23s" in as_text(render_card(card, C, W))


def test_a_ghost_card_is_dimmed_whole():
    card = CardView(handle="w9", origin=Origin(kind="queue"), ghost_since=1.0)
    t = render_card(card, C, W)
    assert any(
        "dim" in str(s.style) and s.start == 0 and s.end == len(t.plain)
        for s in t.spans
    )


def test_a_local_host_is_not_named():
    out = as_text(render_card(CardView(handle="a", agent_slug="opus"), C, W))
    assert "local" not in out


def test_a_remote_host_is_named():
    out = as_text(render_card(CardView(handle="a", host="vps"), C, W))
    assert "vps" in out


def test_an_identity_with_nothing_left_is_omitted():
    lines = as_text(render_card(CardView(handle="a"), C, W)).split("\n")
    assert len(lines) == 2  # the cap and the bottom border


def test_context_over_80_percent_is_drawn_in_the_error_colour():
    t = render_card(CardView(handle="a", ctx_pct=82), C, W)
    start = t.plain.index("ctx 82%")
    assert any(
        s.start <= start and s.end >= start + len("ctx 82%") and str(s.style) == C.err
        for s in t.spans
    )
    calm = render_card(CardView(handle="a", ctx_pct=41), C, W)
    at = calm.plain.index("ctx 41%")
    assert not any(s.start <= at < s.end and str(s.style) == C.err for s in calm.spans)


def test_an_ephemeral_card_carries_the_timer_before_its_handle():
    card = CardView(handle="brisk-babbage", origin=Origin(kind="queue"), tab_index=4)
    cap = as_text(render_card(card, C, W)).split("\n")[0]
    assert "⏱ brisk-babbage" in cap


def test_a_full_footer_keeps_the_monitor_and_sheds_the_slow_parts():
    """A footer is cut from the right. The live monitor must survive; the
    cost and claim count, which the band repeats, are what get cut."""
    card = CardView(
        handle="busy",
        uptime_s=6420,
        ctx_pct=41,
        cost_usd=2.14,
        claims=2,
        monitor="pytest 60%",
        tab_index=1,
    )
    out = as_text(render_card(card, C, 46))
    assert "pytest 60%" in out
