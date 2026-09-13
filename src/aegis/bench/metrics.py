"""Fold one repeat's JSONL into metrics and gates, and repeats into medians.

Everything is restricted to the scenario's measurement window, so boot and
history preload never leak into a streaming number. Gates are computed
here rather than in the scenario, so a summary cannot claim a clean run
that its own raw files contradict.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from pathlib import Path

from aegis.bench.records import read_jsonl

MB = 2**20
NS_MS = 1_000_000


@dataclass(frozen=True)
class MetricSpec:
    """How to read a metric: its unit, which direction is better, the
    absolute change below which a difference is noise, and whether it is a
    timing, a resource figure, or a deterministic count."""
    unit: str
    better: str = "lower"
    floor: float = 0.0
    kind: str = "timing"


def _t(floor: float) -> MetricSpec:
    return MetricSpec("ms", floor=floor)


def _r(unit: str, floor: float, better: str = "lower") -> MetricSpec:
    return MetricSpec(unit, better=better, floor=floor, kind="resource")


METRICS: dict[str, MetricSpec] = {
    **{f"latency.marker_ms.{q}": _t(2.0) for q in ("p50", "p95", "p99", "max")},
    **{f"latency.marker_b_ms.{q}": _t(2.0) for q in ("p50", "p95", "max")},
    **{f"latency.echo_ms.{q}": _t(2.0) for q in ("p50", "p95", "max")},
    **{f"{g}.{m}.{q}": _t(5.0) for g in ("resize", "sidebar")
       for m in ("first_frame_ms", "settle_ms") for q in ("p50", "max")},
    **{f"render.tick_ms.{q}": _t(0.5) for q in ("p50", "p95", "p99", "max")},
    "render.layout_ms.p95": _t(0.5),
    "render.compose_ms.p95": _t(0.5),
    "render.display_ms.p95": _t(0.5),
    "render.paint_ms.p50": _t(0.5),
    "render.ticks_per_s": _r("/s", 2.0),
    "render.frames_per_s": _r("/s", 2.0),
    "render.bytes_per_frame.p50": _r("B", 200),
    "render.height_calls": MetricSpec("calls", kind="count"),
    "render.render_lines_calls": MetricSpec("calls", kind="count"),
    **{f"loop.lag_ms.{q}": _t(1.0) for q in ("p50", "p99", "max")},
    **{f"loop.stalls_{n}_per_min": _r("/min", 1.0) for n in (16, 50, 100)},
    "gc.pause_total_ms": _t(5.0),
    "gc.pause_max_ms": _t(2.0),
    "cpu.daemon_s_per_s": _r("s/s", 0.02),
    "cpu.client_s_per_s": _r("s/s", 0.02),
    "mem.rss_peak_mb": _r("MB", 2.0),
    "mem.rss_end_mb": _r("MB", 2.0),
    "mem.uss_peak_mb": _r("MB", 2.0),
    "mem.rss_growth_mb_per_1k_lines": _r("MB", 0.5),
    "startup.daemon_boot_ms": _t(50.0),
    "startup.first_frame_ms": _t(50.0),
    "startup.ready_ms": _t(50.0),
    "startup.warm_first_frame_ms": _t(20.0),
}


def pct(values: list[float], q: float) -> float | None:
    """Nearest-rank percentile: always a value that was actually observed."""
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, max(0, math.ceil(q * len(s)) - 1))]


def _dist(out: dict, prefix: str, values: list[float],
          qs: tuple[str, ...]) -> None:
    if not values:
        return
    for q in qs:
        name = f"{prefix}.{q}"
        if name not in METRICS:
            continue
        v = max(values) if q == "max" else pct(values, int(q[1:]) / 100)
        out[name] = round(float(v), 3)


def _markers(out: dict, gates: list, client: str, *, since: float,
             end: float, emit: list[dict], frames: list[dict]) -> None:
    first_seen: dict[str, int] = {}
    for f in frames:
        if f["k"] == "frame" and f["client"] == client:
            for m in f["markers"]:
                first_seen.setdefault(m, f["t_ns"])
    lat: list[float] = []
    lost: list[str] = []
    for r in emit:
        if r["k"] != "marker" or not (since <= r["t_emit_ns"] <= end):
            continue
        seen = first_seen.get(r["marker"])
        if seen is None:
            lost.append(r["marker"])
        else:
            lat.append((seen - r["t_emit_ns"]) / NS_MS)
    prefix = ("latency.marker_ms" if client == "a"
              else f"latency.marker_{client}_ms")
    _dist(out, prefix, lat, ("p50", "p95", "p99", "max"))
    detail = f"{len(lost)} of {len(lat) + len(lost)} lost"
    if lost:
        detail += f": {lost[:5]}"
    gates.append({"name": "markers_lost" if client == "a"
                  else f"markers_lost_{client}",
                  "ok": not lost, "detail": detail})


def repeat_metrics(rep_dir: Path) -> tuple[dict[str, float], list[dict]]:
    rep_dir = Path(rep_dir)
    events = read_jsonl(rep_dir / "events.jsonl")
    emit = read_jsonl(rep_dir / "emit.jsonl")
    frames = read_jsonl(rep_dir / "frames.jsonl")
    probe = read_jsonl(rep_dir / "probe.jsonl")
    out: dict[str, float] = {}
    gates: list[dict] = []

    windows = [e for e in events if e["k"] == "window"]
    start = windows[-1]["start_ns"] if windows else 0
    end = windows[-1]["end_ns"] if windows else float("inf")
    span_s = (end - start) / 1e9 if windows else None

    def inside(t: float) -> bool:
        return start <= t <= end

    ready = {e["client"]: e["t_ns"] for e in events
             if e["k"] == "client_ready"}
    for exp in (e for e in events if e["k"] == "expect_markers"):
        client = exp["client"]
        _markers(out, gates, client, since=max(start, ready.get(client, 0)),
                 end=end, emit=emit, frames=frames)

    fa = [f for f in frames if f["k"] == "frame" and f["client"] == "a"
          and inside(f["t_ns"])]
    if fa:
        _dist(out, "render.bytes_per_frame", [f["nbytes"] for f in fa],
              ("p50",))
        if span_s:
            out["render.frames_per_s"] = round(len(fa) / span_s, 3)

    ticks = [r for r in probe if r["k"] == "tick" and inside(r["t0"])]
    if ticks:
        _dist(out, "render.tick_ms", [r["dur_ns"] / NS_MS for r in ticks],
              ("p50", "p95", "p99", "max"))
        for part in ("layout", "compose", "display"):
            _dist(out, f"render.{part}_ms",
                  [r[part] / NS_MS for r in ticks], ("p95",))
        if span_s:
            out["render.ticks_per_s"] = round(len(ticks) / span_s, 3)
    _dist(out, "render.paint_ms", [r["dur_ns"] / NS_MS for r in probe
                                   if r["k"] == "paint" and inside(r["t0"])],
          ("p50",))

    lags = [r["lag_ns"] / NS_MS for r in probe
            if r["k"] == "lag" and inside(r["t_ns"])]
    if lags:
        _dist(out, "loop.lag_ms", lags, ("p50", "p99", "max"))
        minutes = (span_s or len(lags) * 0.005) / 60
        for n in (16, 50, 100):
            out[f"loop.stalls_{n}_per_min"] = round(
                sum(1 for v in lags if v > n) / minutes, 3)

    samples = [r for r in probe if r["k"] == "sample" and inside(r["t_ns"])]
    if len(samples) >= 2:
        a, b = samples[0], samples[-1]
        dt = (b["t_ns"] - a["t_ns"]) / 1e9
        if dt > 0:
            out["cpu.daemon_s_per_s"] = round(
                (b["cpu_s"] - a["cpu_s"]) / dt, 4)
        out["render.height_calls"] = b["n_height"] - a["n_height"]
        out["render.render_lines_calls"] = (b["n_render_lines"]
                                            - a["n_render_lines"])
        out["gc.pause_total_ms"] = round(
            (b["gc_ns_total"] - a["gc_ns_total"]) / NS_MS, 3)
        out["gc.pause_max_ms"] = round(b["gc_ns_max"] / NS_MS, 3)
    rss = [s["rss"] for s in samples if s.get("rss")]
    if rss:
        out["mem.rss_peak_mb"] = round(max(rss) / MB, 2)
        out["mem.rss_end_mb"] = round(rss[-1] / MB, 2)
    uss = [s["uss"] for s in samples if s.get("uss")]
    if uss:
        out["mem.uss_peak_mb"] = round(max(uss) / MB, 2)

    procs = [f for f in frames if f["k"] == "proc" and f["client"] == "a"
             and inside(f["t_ns"])]
    if len(procs) >= 2:
        dt = (procs[-1]["t_ns"] - procs[0]["t_ns"]) / 1e9
        if dt > 0:
            out["cpu.client_s_per_s"] = round(
                (procs[-1]["cpu_s"] - procs[0]["cpu_s"]) / dt, 4)

    grouped: dict[str, list[float]] = {}
    for e in events:
        if e["k"] == "metric" and e["name"] in METRICS:
            out[e["name"]] = e["value"]
        elif e["k"] == "sample_ms":
            grouped.setdefault(e["name"], []).append(e["value"])
    for name, values in grouped.items():
        _dist(out, name, values, ("p50", "p95", "max"))

    gate_recs = [r for r in probe if r["k"] == "gate"]
    if gate_recs:
        g = gate_recs[-1]
        gates.append({"name": "probe_sync", "ok": bool(g["sync"]),
                      "detail": "app._sync_available"})
        gates.append({"name": "not_headless", "ok": not g["headless"],
                      "detail": "app.is_headless"})
    gates.extend({"name": e["name"], "ok": bool(e["ok"]),
                  "detail": e.get("detail", "")}
                 for e in events if e["k"] == "gate")
    return out, gates


def summarize(run_id: str, fingerprint: dict,
              results: dict[str, dict]) -> dict:
    scenarios = {}
    for name, res in results.items():
        keys = sorted({k for rep in res["repeats"] for k in rep})
        median = {k: round(statistics.median(
            [rep[k] for rep in res["repeats"] if k in rep]), 4) for k in keys}
        scenarios[name] = {**res, "median": median}
    return {"schema": 1, "run_id": run_id, "fingerprint": fingerprint,
            "scenarios": scenarios}
