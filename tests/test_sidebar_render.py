"""The pure sidebar renderer.

Sections are ordered by volatility, highest first: on a short terminal the
panel scrolls, and what you see without scrolling should be what moves.
An empty section renders nothing at all — not a heading over a blank.
"""
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
