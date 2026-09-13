"""Per-metric verdicts between two summaries.

A change counts only when it is larger than 10% of the baseline, larger
than the metric's absolute floor, and the two runs' repeat ranges do not
overlap. All three, because each alone is fooled by something ordinary:
a relative threshold by tiny baselines, a floor by large ones, and range
separation by a single noisy repeat.
"""
from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass
from pathlib import Path

from aegis.bench import BenchError
from aegis.bench.metrics import METRICS, MetricSpec
from aegis.bench.runner import history_dir, runs_dir

REL = 0.10


class CompareError(BenchError):
    pass


@dataclass(frozen=True)
class Row:
    metric: str
    a: float | None
    b: float | None
    delta_pct: float | None
    verdict: str


def verdict(spec: MetricSpec, a: list[float], b: list[float]) -> str:
    ma, mb = statistics.median(a), statistics.median(b)
    if spec.kind == "count":
        return "same" if ma == mb else "changed"
    diff = mb - ma
    if abs(diff) <= spec.floor:
        return "noise"
    if ma and abs(diff) / abs(ma) <= REL:
        return "noise"
    if min(b) <= max(a) and min(a) <= max(b):
        return "noise"
    worse = diff > 0 if spec.better == "lower" else diff < 0
    return "regressed" if worse else "improved"


def compare(a: dict, b: dict, *, force: bool = False) -> dict[str, list[Row]]:
    fa, fb = a["fingerprint"], b["fingerprint"]
    for key in ("host", "size"):
        if fa.get(key) != fb.get(key) and not force:
            raise CompareError(f"{key} differs ({fa.get(key)} vs "
                               f"{fb.get(key)}); pass --force to compare")
    out: dict[str, list[Row]] = {}
    for name in sorted(set(a["scenarios"]) & set(b["scenarios"])):
        ra = a["scenarios"][name]["repeats"]
        rb = b["scenarios"][name]["repeats"]
        rows = []
        for m in sorted({k for rep in ra + rb for k in rep}):
            va = [rep[m] for rep in ra if m in rep]
            vb = [rep[m] for rep in rb if m in rep]
            if not va or not vb:
                rows.append(Row(m, statistics.median(va) if va else None,
                                statistics.median(vb) if vb else None, None,
                                "new" if vb else "gone"))
                continue
            ma, mb = statistics.median(va), statistics.median(vb)
            delta = (mb - ma) / ma * 100 if ma else None
            spec = METRICS.get(m, MetricSpec("?"))
            rows.append(Row(m, ma, mb, delta, verdict(spec, va, vb)))
        out[name] = rows
    return out


def load_summary(ref: str) -> dict:
    """A summary by path to ``summary.json``, run directory, run id under
    ``runs_dir()``, or a saved history file."""
    p = Path(ref).expanduser()
    for cand in (p, p / "summary.json", runs_dir() / ref / "summary.json"):
        if cand.is_file():
            return json.loads(cand.read_text())
    raise BenchError(f"no summary found for {ref!r}")


def version_key(path: Path) -> tuple[int, ...] | None:
    """``X.Y.Z`` of a release history file; None for a dev snapshot, whose
    name carries a sha suffix and must never pose as a release."""
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", path.stem)
    return tuple(int(x) for x in m.groups()) if m else None


def latest_release(host: str | None = None) -> dict | None:
    base = history_dir() if host is None else history_dir().parent / host
    files = [p for p in base.glob("*.json") if version_key(p)]
    if not files:
        return None
    return json.loads(max(files, key=version_key).read_text())
