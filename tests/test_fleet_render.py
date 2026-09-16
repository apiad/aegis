"""The pure card renderer. Chrome is English; content is the session's own."""

from aegis.fleet.models import CardView, EventLine, Origin
from aegis.fleet.render import render_card
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
