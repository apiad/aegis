import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "bench_compare.py"
spec = importlib.util.spec_from_file_location("bench_compare", SCRIPT)
bc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bc)


def test_only_a_regression_past_20_percent_warns():
    base = {
        "cold_load_ms": 100.0,
        "server_line_p50_us": 50.0,
        "cold_load_entries": 2000,
    }
    head = {
        "cold_load_ms": 125.0,
        "server_line_p50_us": 59.0,
        "cold_load_entries": 4000,
    }
    rows, warnings = bc.compare(base, head)
    assert warnings == [
        "::warning title=aegis bench::cold_load_ms is 25% worse (100.00 → 125.00)"
    ]
    assert any("+18%" in r for r in rows)


def test_no_base_reports_head_alone():
    rows, warnings = bc.compare(None, {"cold_load_ms": 1.0})
    assert warnings == [] and "No base result" in "\n".join(rows)


def test_a_change_inside_the_samples_spread_does_not_warn():
    base = {"cold_load_ms": 100.0}
    head = {"cold_load_ms": 130.0}
    spread = {"cold_load_ms": [90.0, 100.0, 140.0]}
    _, warnings = bc.compare(
        base, head, spread, {"cold_load_ms": [125.0, 130.0, 160.0]}
    )
    assert warnings == []
    _, warnings = bc.compare(
        base, head, spread, {"cold_load_ms": [145.0, 130.0, 160.0]}
    )
    assert warnings == []  # head's best (130) is below base's worst (140)
    _, warnings = bc.compare(
        base, head, {"cold_load_ms": [95.0, 100.0]}, {"cold_load_ms": [125.0, 135.0]}
    )
    assert len(warnings) == 1


def test_runs_in_a_directory_merge_into_medians_and_pooled_samples(tmp_path):
    import json

    for i, (v, s) in enumerate([(100.0, [99.0, 101.0]), (120.0, [119.0]), (90.0, [])]):
        run = {"metrics": {"cold_load_ms": v, "server_rss_mb": v / 10}}
        if s:
            run["samples"] = {"cold_load_ms": s}
        (tmp_path / f"{i}.json").write_text(json.dumps(run))
    metrics, samples = bc.load(str(tmp_path))
    assert metrics == {"cold_load_ms": 100.0, "server_rss_mb": 10.0}
    # A run without samples for a metric contributes its figure.
    assert sorted(samples["cold_load_ms"]) == [90.0, 99.0, 101.0, 119.0]
    assert sorted(samples["server_rss_mb"]) == [9.0, 10.0, 12.0]


def test_a_few_kilobytes_more_on_a_small_transfer_does_not_warn():
    _, warnings = bc.compare({"cold_saved_kb": 2.0}, {"cold_saved_kb": 6.0})
    assert warnings == []
    _, warnings = bc.compare({"cold_saved_kb": 2.0}, {"cold_saved_kb": 660.0})
    assert len(warnings) == 1
