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


# --- gate: baseline, candidate, baseline again (#43) --------------------------


def _runs(*values, metric="render.tick_ms.p50"):
    return _summary(reps=tuple({metric: v} for v in values))


def test_gate_confirms_a_regression_past_both_baselines_that_agree():
    from aegis.bench.compare import gate

    confirmed, dropped = gate(
        _runs(4.0, 4.1, 4.05), _runs(6.0, 6.1, 6.2), _runs(4.02, 4.08, 4.1)
    )
    assert [(sc, r.metric) for sc, r in confirmed] == [("x", "render.tick_ms.p50")]
    assert dropped == []


def test_gate_drops_a_metric_the_baseline_moved_on_between_its_own_runs():
    """The runner slowed down mid-job: the candidate and the second baseline
    both read worse than the first, and that is the machine, not the code."""
    from aegis.bench.compare import gate

    confirmed, dropped = gate(
        _runs(4.0, 4.1, 4.05), _runs(6.0, 6.1, 6.2), _runs(6.05, 6.1, 6.15)
    )
    assert confirmed == []
    assert dropped == [("x", "render.tick_ms.p50")]


def test_gate_drops_a_regression_against_only_one_baseline():
    from aegis.bench.compare import gate

    confirmed, dropped = gate(
        _runs(4.0, 4.1, 4.05), _runs(6.0, 6.1, 6.2), _runs(4.0, 4.1, 6.3)
    )
    assert confirmed == []
    assert dropped == [("x", "render.tick_ms.p50")]


def test_gate_never_confirms_an_improvement():
    from aegis.bench.compare import gate

    confirmed, dropped = gate(
        _runs(6.0, 6.1, 6.2), _runs(4.0, 4.1, 4.05), _runs(6.0, 6.1, 6.2)
    )
    assert confirmed == [] and dropped == []


def test_gate_command_exits_on_a_confirmed_regression_only(tmp_path):
    """Through the CLI, including a zero baseline: stalls going from 0 to 8
    have no percentage, and printing one crashed the command."""
    import json

    from typer.testing import CliRunner

    from aegis.cli_bench import app

    def write(name, *values):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(_runs(*values, metric="loop.stalls_100_per_min")))
        return str(path)

    base1, base2 = write("b1", 0, 0, 0), write("b2", 0, 0, 0)
    bad, same = write("bad", 8, 9, 8), write("same", 0, 0, 0)
    runner = CliRunner()
    hit = runner.invoke(app, ["gate", base1, bad, base2])
    assert hit.exit_code == 1, hit.output
    assert "loop.stalls_100_per_min: 0 -> 8" in hit.output
    clean = runner.invoke(app, ["gate", base1, same, base2])
    assert clean.exit_code == 0, clean.output
