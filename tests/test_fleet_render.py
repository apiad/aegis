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
