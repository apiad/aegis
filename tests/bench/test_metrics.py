import json

from aegis.bench.metrics import METRICS, pct, repeat_metrics, summarize

MS = 1_000_000


def _w(path, recs):
    path.write_text("".join(json.dumps(r) + "\n" for r in recs))


def test_pct_nearest_rank():
    assert pct([], 0.5) is None
    assert pct([3, 1, 2], 0.5) == 2
    assert pct(list(range(1, 101)), 0.99) == 99


def test_marker_latency_joins_emit_to_first_frame_in_window(tmp_path):
    _w(tmp_path / "events.jsonl", [
        {"k": "window", "start_ns": 100 * MS, "end_ns": 1000 * MS},
        {"k": "expect_markers", "client": "a"}])
    _w(tmp_path / "emit.jsonl", [
        {"k": "marker", "marker": "«b0001»", "t_emit_ns": 50 * MS},
        {"k": "marker", "marker": "«b0002»", "t_emit_ns": 200 * MS},
        {"k": "marker", "marker": "«b0003»", "t_emit_ns": 300 * MS}])
    _w(tmp_path / "frames.jsonl", [
        {"k": "frame", "client": "a", "t_ns": 230 * MS, "nbytes": 10,
         "markers": ["«b0002»"]},
        {"k": "frame", "client": "a", "t_ns": 260 * MS, "nbytes": 30,
         "markers": ["«b0002»"]},
        {"k": "frame", "client": "a", "t_ns": 340 * MS, "nbytes": 20,
         "markers": ["«b0003»"]}])
    m, gates = repeat_metrics(tmp_path)
    # «b0001» was emitted before the window opened and is not counted.
    assert m["latency.marker_ms.p50"] == 30.0
    assert m["latency.marker_ms.max"] == 40.0
    assert m["latency.markers_undrawn_pct"] == 0.0
    assert {g["name"]: g["ok"] for g in gates}["markers_seen"] is True


def test_no_marker_drawn_fails_the_gate(tmp_path):
    _w(tmp_path / "events.jsonl", [{"k": "expect_markers", "client": "a"}])
    _w(tmp_path / "emit.jsonl", [
        {"k": "marker", "marker": "«b0001»", "t_emit_ns": 1}])
    _w(tmp_path / "frames.jsonl", [])
    _, gates = repeat_metrics(tmp_path)
    seen = next(g for g in gates if g["name"] == "markers_seen")
    assert seen["ok"] is False and "«b0001»" in seen["detail"]


def test_second_client_counts_only_markers_after_it_was_ready(tmp_path):
    _w(tmp_path / "events.jsonl", [
        {"k": "expect_markers", "client": "b"},
        {"k": "client_ready", "client": "b", "t_ns": 150 * MS}])
    _w(tmp_path / "emit.jsonl", [
        {"k": "marker", "marker": "«b0001»", "t_emit_ns": 100 * MS},
        {"k": "marker", "marker": "«b0002»", "t_emit_ns": 200 * MS}])
    _w(tmp_path / "frames.jsonl", [
        {"k": "frame", "client": "b", "t_ns": 210 * MS, "nbytes": 1,
         "markers": ["«b0002»"]}])
    m, gates = repeat_metrics(tmp_path)
    assert m["latency.marker_b_ms.p50"] == 10.0
    assert {g["name"]: g["ok"] for g in gates}["markers_seen_b"] is True


def test_probe_ticks_lag_and_samples(tmp_path):
    _w(tmp_path / "events.jsonl", [
        {"k": "window", "start_ns": 0, "end_ns": 60_000 * MS}])
    _w(tmp_path / "probe.jsonl", [
        {"k": "tick", "t0": 10 * MS, "dur_ns": 4 * MS, "layout": 2 * MS,
         "compose": 1 * MS, "display": 1 * MS, "n_height": 3,
         "n_render_lines": 5},
        {"k": "lag", "t_ns": 20 * MS, "lag_ns": 60 * MS},
        {"k": "lag", "t_ns": 30 * MS, "lag_ns": 1 * MS},
        {"k": "sample", "t_ns": 0, "cpu_s": 1.0, "rss": 100 * 2**20,
         "uss": 80 * 2**20, "n_height": 0, "n_render_lines": 0,
         "gc_ns_total": 0, "gc_ns_max": 0},
        {"k": "sample", "t_ns": 60_000 * MS, "cpu_s": 7.0,
         "rss": 120 * 2**20, "uss": 90 * 2**20, "n_height": 30,
         "n_render_lines": 50, "gc_ns_total": 5 * MS, "gc_ns_max": 2 * MS},
        {"k": "gate", "sync": True, "headless": False, "aegis_file": "x"}])
    _w(tmp_path / "emit.jsonl", [])
    _w(tmp_path / "frames.jsonl", [])
    m, gates = repeat_metrics(tmp_path)
    assert m["render.tick_ms.p50"] == 4.0
    assert m["loop.stalls_50_per_min"] == 1.0
    assert m["cpu.daemon_s_per_s"] == 0.1
    assert m["mem.rss_peak_mb"] == 120.0
    assert m["render.height_calls"] == 30
    assert m["gc.pause_max_ms"] == 2.0
    names = {g["name"]: g["ok"] for g in gates}
    assert names["probe_sync"] and names["not_headless"]


def test_headless_probe_fails_its_gate(tmp_path):
    _w(tmp_path / "probe.jsonl", [
        {"k": "gate", "sync": False, "headless": True, "aegis_file": "x"}])
    _, gates = repeat_metrics(tmp_path)
    names = {g["name"]: g["ok"] for g in gates}
    assert names["probe_sync"] is False and names["not_headless"] is False


def test_every_emitted_metric_has_a_spec(tmp_path):
    _w(tmp_path / "events.jsonl", [
        {"k": "metric", "name": "startup.first_frame_ms", "value": 5.0},
        {"k": "sample_ms", "name": "resize.first_frame_ms", "value": 7.0}])
    m, _ = repeat_metrics(tmp_path)
    assert m
    assert set(m) <= set(METRICS)


def test_summarize_takes_median_over_repeats():
    s = summarize("r", {"host": "h"}, {"x": {
        "status": "ok", "reason": "", "gates": [[], [], []],
        "repeats": [{"a": 1.0}, {"a": 9.0}, {"a": 3.0}]}})
    assert s["scenarios"]["x"]["median"] == {"a": 3.0}
    assert s["schema"] == 1


def test_partly_undrawn_markers_are_data_not_a_failure(tmp_path):
    # A reply rendered in one piece at turn end draws only its tail: the
    # markers above the fold were never on screen. Measured on ACP.
    _w(tmp_path / "events.jsonl", [{"k": "expect_markers", "client": "a"}])
    _w(tmp_path / "emit.jsonl", [
        {"k": "marker", "marker": "«b0001»", "t_emit_ns": 1 * MS},
        {"k": "marker", "marker": "«b0002»", "t_emit_ns": 2 * MS}])
    _w(tmp_path / "frames.jsonl", [
        {"k": "frame", "client": "a", "t_ns": 5 * MS, "nbytes": 1,
         "markers": ["«b0002»"]}])
    m, gates = repeat_metrics(tmp_path)
    assert m["latency.markers_undrawn_pct"] == 50.0
    assert m["latency.marker_ms.p50"] == 3.0
    assert {g["name"]: g["ok"] for g in gates}["markers_seen"] is True


def test_host_load_is_reported_per_core(tmp_path):
    # Measured 2026-09-13: the same block stream went from p50 28 ms to
    # 141 ms at load 11 on 8 cores. The load has to travel with the numbers.
    _w(tmp_path / "events.jsonl", [
        {"k": "load", "t_ns": 1, "l1": 2.0, "cores": 8},
        {"k": "load", "t_ns": 2, "l1": 11.0, "cores": 8}])
    m, _ = repeat_metrics(tmp_path)
    assert m["host.load_per_core.max"] == 1.375


def test_host_cpu_busy_share_comes_from_the_window_edges(tmp_path):
    # Load average lags by design; /proc/stat at the window edges measures
    # exactly how contended the window was.
    _w(tmp_path / "events.jsonl", [
        {"k": "load", "t_ns": 1, "l1": 1.0, "cores": 8,
         "cpu_idle": 1000, "cpu_total": 2000},
        {"k": "load", "t_ns": 2, "l1": 1.0, "cores": 8,
         "cpu_idle": 1100, "cpu_total": 2400}])
    m, _ = repeat_metrics(tmp_path)
    # 400 jiffies passed, 100 of them idle: 75% busy.
    assert m["host.cpu_busy_pct"] == 75.0
