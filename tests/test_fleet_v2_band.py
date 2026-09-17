from aegis.fleet.models import BandView, CardView, FleetSnapshot, QuotaGauge
from aegis.fleet.render import render_band
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
