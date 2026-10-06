from rich.cells import cell_len

from aegis.fleet.render import blink, bar, ctx_style, pulse, severity_style, sweep_bar
from aegis.tui.themes import INK, aegis_colors

P = aegis_colors(INK)


def test_a_bar_is_exactly_its_width_and_fills_proportionally():
    t = bar(60, 10, P.accent, P)
    assert t.cell_len == 10
    assert t.plain == "██████░░░░"


def test_a_bar_clamps_out_of_range_values():
    assert bar(-5, 4, P.ready, P).plain == "░░░░"
    assert bar(250, 4, P.ready, P).plain == "████"


def test_a_sweep_moves_one_step_per_frame_and_keeps_its_width():
    a, b = sweep_bar(12, 0, P), sweep_bar(12, 1, P)
    assert a.cell_len == b.cell_len == 12
    assert a.plain != b.plain
    assert a.plain.index("█") + 1 == b.plain.index("█")


def test_pulse_alternates_with_muted():
    assert pulse(P.working, 0, P) == P.working
    assert pulse(P.working, 1, P) == P.muted


def test_blink_keeps_the_cells():
    assert blink("94%", 0) == "94%"
    assert blink("94%", 1) == "   "
    assert cell_len(blink("✗", 1)) == cell_len("✗")


def test_severity_and_context_colours():
    assert severity_style("critical", P) == P.error
    assert severity_style("warning", P) == P.accent
    assert severity_style("normal", P) == P.ready
    assert ctx_style(92, P) == P.error and ctx_style(65, P) == P.accent and ctx_style(10, P) == P.ready
