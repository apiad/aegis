import pytest

from aegis.bench.compare import CompareError, compare, min_repeats, verdict
from aegis.bench.metrics import MetricSpec

T = MetricSpec("ms", floor=0.5)


def test_regressed_and_improved_need_size_floor_and_separation():
    assert verdict(T, [10, 10.2, 10.1], [12, 12.3, 12.1]) == "regressed"
    assert verdict(T, [12, 12.3, 12.1], [10, 10.2, 10.1]) == "improved"


def test_under_ten_percent_is_noise():
    assert verdict(T, [10, 10.5, 10.2], [10.6, 10.9, 10.7]) == "noise"


def test_under_the_absolute_floor_is_noise():
    assert verdict(T, [1.0, 1.1], [1.3, 1.4]) == "noise"


def test_overlapping_repeat_ranges_are_noise():
    assert verdict(T, [10, 14], [13, 16]) == "noise"


def test_counts_compare_exactly():
    c = MetricSpec("calls", kind="count")
    assert verdict(c, [30, 30], [30, 30]) == "same"
    assert verdict(c, [30], [45]) == "changed"


def test_higher_is_better_flips_the_direction():
    fps = MetricSpec("/s", better="higher", floor=2.0, kind="resource")
    assert verdict(fps, [30, 31], [50, 52]) == "improved"
    assert verdict(fps, [50, 52], [30, 31]) == "regressed"


def test_zero_baseline_relies_on_floor_and_separation():
    stalls = MetricSpec("/min", floor=1.0, kind="resource")
    assert verdict(stalls, [0, 0], [0.5, 0.5]) == "noise"
    assert verdict(stalls, [0, 0], [8, 9]) == "regressed"


def _summary(host="h", size="120x40", reps=({"render.tick_ms.p50": 4.0},)):
    return {"fingerprint": {"host": host, "size": size, "aegis_build": "x"},
            "scenarios": {"x": {"status": "ok", "repeats": list(reps)}}}


def test_refuses_different_hosts_unless_forced():
    with pytest.raises(CompareError):
        compare(_summary(host="a"), _summary(host="b"))
    assert compare(_summary(host="a"), _summary(host="b"), force=True)


def test_refuses_different_terminal_sizes_unless_forced():
    with pytest.raises(CompareError):
        compare(_summary(size="120x40"), _summary(size="80x24"))


def test_new_and_gone_metrics():
    rows = compare(_summary(reps=({"render.tick_ms.p50": 4.0},)),
                   _summary(reps=({"render.tick_ms.p95": 4.0},)))["x"]
    got = {r.metric: r.verdict for r in rows}
    assert got == {"render.tick_ms.p50": "gone", "render.tick_ms.p95": "new"}


def test_delta_percent_is_relative_to_the_baseline():
    rows = compare(_summary(reps=({"render.tick_ms.p50": 4.0},)),
                   _summary(reps=({"render.tick_ms.p50": 5.0},)))["x"]
    assert rows[0].delta_pct == pytest.approx(25.0)


def test_neutral_metrics_report_changed_never_a_direction():
    # Fewer frames for the same workload is neither better nor worse.
    fps = MetricSpec("/s", better="neutral", floor=2.0, kind="resource")
    assert verdict(fps, [30, 31], [50, 52]) == "changed"
    assert verdict(fps, [50, 52], [30, 31]) == "changed"
    assert verdict(fps, [30, 31], [30.5, 31]) == "noise"


def test_min_repeats_counts_the_thinner_side():
    a = _summary(reps=({"render.tick_ms.p50": 4.0},) * 3)
    b = _summary(reps=({"render.tick_ms.p50": 4.0},))
    assert min_repeats(a, b) == 1
    assert min_repeats(a, a) == 3
