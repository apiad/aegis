from rich.cells import cell_len
from rich.text import Text

from aegis.fleet.models import BandView, CardView, FleetSnapshot, QuotaGauge
from aegis.fleet.render import gauge, render_band, reset_in, rows_of
from aegis.tui.sysmeter import SystemStats
from aegis.tui.themes import INK, aegis_colors

P = aegis_colors(INK)
BAND = BandView(
    host="zion",
    stats=SystemStats(64, 56, 32, 17.9, 32.0),
    ctx_avg=47.0,
    gauges=(
        QuotaGauge("cc 5h", 38, "normal", 8040),
        QuotaGauge("oc mo", 94, "critical", None),
    ),
    cost_live=41.2,
    clock="01:12",
)
CARDS = (
    CardView(handle="a", state="working"),
    CardView(handle="b", state="ready", attention="needs_input"),
    CardView(handle="c", state="ready", attention="error"),
    CardView(handle="d", state="ready", attention="review"),
    CardView(handle="e", state="ready", attention="waiting"),
    CardView(handle="f", state="ready"),
    CardView(handle="g", state="ready", ghost_since=1.0),
)


def _lines(width=160, frame=0):
    return render_band(FleetSnapshot(band=BAND, cards=CARDS), P, width, frame).plain.split("\n")


def test_three_rows_host_quota_counters():
    lines = _lines()
    assert lines[0].startswith("CPU") and "RAM" in lines[0] and "17.9/32G" in lines[0] and "CTX" in lines[0]
    assert "cc 5h" in lines[1] and "↻ 2h14m" in lines[1] and "oc mo" in lines[1]
    assert "zion" in lines[2]


def test_counters_follow_attention_and_skip_ghosts():
    row = _lines()[2]
    for expected in ("1 working", "1 need you", "1 error", "1 review", "1 waiting", "1 done"):
        assert expected in row


def test_no_row_is_wider_than_the_screen():
    for width in (100, 160, 220):
        assert all(len(line) <= width for line in _lines(width))


def test_a_critical_quota_blinks_and_a_normal_one_does_not():
    on, off = _lines(frame=0)[1], _lines(frame=1)[1]
    assert "94%" in on and "94%" not in off
    assert "38%" in on and "38%" in off


def test_no_quota_draws_no_quota_row():
    from dataclasses import replace

    t = render_band(FleetSnapshot(band=replace(BAND, gauges=()), cards=CARDS), P, 160, 0)
    assert "cc 5h" not in t.plain and len(t.plain.rstrip("\n").split("\n")) == 2


def test_a_narrow_screen_puts_two_gauges_per_line():
    lines = _lines(width=100)
    assert lines[0].startswith("CPU") and "DSK" not in lines[0]
    assert any(line.startswith("DSK") for line in lines)


def test_a_nonzero_error_or_need_you_counter_blinks_in_place():
    on, off = _lines(frame=0)[2], _lines(frame=1)[2]
    assert "✗" in on and "✗" not in off
    assert "?" in on and "?" not in off
    assert cell_len(on) == cell_len(off)
    assert on.index("1 error") == off.index("1 error")


def test_a_six_cell_label_keeps_a_space_before_its_bar():
    from dataclasses import replace

    band = replace(BAND, gauges=(QuotaGauge("oc wk1", 50, "normal", None),))
    row = render_band(FleetSnapshot(band=band, cards=CARDS), P, 160, 0).plain.split("\n")[1]
    assert row.startswith("oc wk1 ")


def test_a_single_quota_gauge_takes_the_whole_narrow_line():
    from dataclasses import replace

    band = replace(BAND, gauges=(QuotaGauge("cc 5h", 38, "normal", 8040),))
    row = render_band(FleetSnapshot(band=band, cards=CARDS), P, 100, 0).plain.split("\n")[2]
    assert row.startswith("cc 5h") and cell_len(row) > 60


def test_a_narrow_counters_row_drops_whole_segments():
    row = _lines(width=100)[-1]
    assert cell_len(row) <= 100
    assert row.endswith(("1 done", "live", "queues 0/0", "monitors 0", "01:12"))
    assert not row.endswith(" · ")


def test_quota_gauges_wrap_two_per_line_on_a_narrow_screen():
    from dataclasses import replace

    band = replace(BAND, gauges=BAND.gauges + (QuotaGauge("oc wk", 10, "normal", 50),))
    lines = render_band(FleetSnapshot(band=band, cards=CARDS), P, 100, 0).plain.split("\n")
    first_quota = lines[2]
    assert sum(label in first_quota for label in ("cc 5h", "oc mo", "oc wk")) == 2
    assert lines[3].startswith("oc wk")


def test_a_gauge_is_one_row_and_never_wider_than_its_budget():
    """The property the whole sidebar redesign rests on: a gauge replaces a
    text row rather than adding one, so it must be exactly one row, and it
    must fit the column it was given or the section below it shifts."""
    for cells in range(10, 61):
        g = gauge("CTX", 71.0, "71%", P.accent, cells, P, tail="142k/200k")
        assert "\n" not in g.plain
        assert cell_len(g.plain) <= cells, f"overflowed at {cells}"


def test_reset_in_is_empty_for_an_unknown_reset():
    assert reset_in(None) == ""
    assert reset_in(3840) == "↻ 1h04m"


def test_rows_of_pairs_gauges_two_to_a_line():
    a, b, c = (Text("a"), Text("b"), Text("c"))
    assert rows_of([a, b, c], 2).plain.split("\n") == ["a  b", "c"]
