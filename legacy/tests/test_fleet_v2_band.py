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


def test_a_countdown_past_a_day_is_counted_in_days():
    # A weekly window four days out. In hours this is `↻ 105h34m`, nine cells.
    assert reset_in(380068) == "↻ 4d09h"


def test_rows_of_pairs_gauges_two_to_a_line():
    a, b, c = (Text("a"), Text("b"), Text("c"))
    assert rows_of([a, b, c], 2).plain.split("\n") == ["a  b", "c"]


# --- pace: the projection earns its cells only when it decides the colour ----


def test_a_gauge_past_the_pace_threshold_prints_its_projection():
    from dataclasses import replace

    band = replace(BAND, gauges=(QuotaGauge("cc 5h", 45, "critical", 12600, 150.0),))
    row = render_band(FleetSnapshot(band=band, cards=CARDS), P, 160, 0).plain.split("\n")[1]
    assert "45%" in row and "→150%" in row and "↻ 3h30m" in row


def test_a_comfortable_gauge_keeps_its_projection_off_the_screen():
    from dataclasses import replace

    band = replace(BAND, gauges=(QuotaGauge("cc 5h", 30, "normal", 7200, 50.0),))
    row = render_band(FleetSnapshot(band=band, cards=CARDS), P, 160, 0).plain.split("\n")[1]
    assert "30%" in row and "↻ 2h00m" in row and "→" not in row


def test_five_gauges_with_projections_still_leave_a_bar_to_read():
    """Five gauges is a real band: two providers, five bar windows between them.
    Measured with a *weekly* reset, which is the widest tail one carries."""
    from dataclasses import replace

    gauges = tuple(
        QuotaGauge(f"cc w{i}", 45, "critical", 380068, 150.0) for i in range(5)
    )
    band = replace(BAND, gauges=gauges)
    row = render_band(FleetSnapshot(band=band, cards=CARDS), P, 160, 0).plain.split("\n")[1]
    assert cell_len(row) <= 160
    assert "→150%" in row and "↻ 4d09h" in row
    # The property is bar *cells*, not lit ones: on a three-cell floor 45% is a
    # single lit block, which reads as a glyph rather than a bar. This tail
    # leaves five cells each, which is two lit at 45%.
    assert (row.count("█") + row.count("░")) >= 5 * 5


def test_the_middle_band_reads_as_yellow_and_not_as_the_error_colour():
    """`accent` and `warning` are the same amber in ink and slate, so the old
    mapping only showed up in parchment, where accent is a terracotta next to
    the error red."""
    from aegis.fleet.render import severity_style
    from aegis.tui.themes import PARCHMENT, aegis_colors

    pal = aegis_colors(PARCHMENT)
    assert pal.accent != pal.working  # or this test proves nothing
    assert severity_style("warning", pal) == pal.working
    assert severity_style("critical", pal) == pal.error
    assert severity_style("normal", pal) == pal.ready


# --- #41: a provider with no reading, and a stale one -----------------------


def test_the_band_prints_a_provider_without_a_reading_as_its_reason():
    from dataclasses import replace

    band = replace(
        BAND,
        gauges=(
            QuotaGauge("cc", 0.0, "normal", None, note="rate limited"),
            QuotaGauge("oc wk", 57, "warning", 60),
        ),
    )
    row = render_band(FleetSnapshot(band=band, cards=CARDS), P, 160, 0).plain.split("\n")[1]
    assert row.startswith("cc") and "rate limited" in row
    assert "0%" not in row.split("oc wk")[0]
    assert "oc wk" in row and "57%" in row
    assert cell_len(row) <= 160


def test_a_stale_gauge_says_so_in_its_tail():
    from aegis.fleet.render import quota_tail

    assert quota_tail(QuotaGauge("cc 5h", 14, "normal", 3600, stale=True)).endswith("(stale)")
    assert "stale" not in quota_tail(QuotaGauge("cc 5h", 14, "normal", 3600))
