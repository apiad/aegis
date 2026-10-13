"""Compare two scripts/bench.py results. Reports; never fails.

    uv run python scripts/bench_compare.py BASE HEAD

BASE and HEAD are each a result file or a directory of them; a directory's
runs merge into each metric's median and the pool of their samples, so CI can
alternate base and head and neither pays for running second (#282).

Writes a Markdown table to $GITHUB_STEP_SUMMARY when it is set, else to
stdout, and prints a GitHub ``::warning::`` line on stdout for every metric
more than 20% worse whose best head sample is also worse than base's worst
(and, for a ``*_kb`` metric, more than 10 KB larger):
a change inside the metric's own spread is noise, and warning on it trained
readers to skip the warnings. Every measured metric is lower-is-better;
counts (``*_entries``, ``*_lines``) are context. A missing BASE prints HEAD
alone.
"""

from __future__ import annotations

import json
import os
import statistics
import sys
from pathlib import Path

WORSE = 0.20
# A byte metric must also grow by this much: what a reload receives when the
# device kept a copy is 1 to 6 KB depending on what changed in the moment, and
# a regression worth seeing is the 660 KB snapshot coming back.
KB_FLOOR = 10.0


def load(path: str) -> tuple[dict, dict] | None:
    """The median of each metric over the runs at ``path``, and each metric's
    samples pooled; a run that kept none for a metric gives its figure."""
    p = Path(path)
    files = sorted(p.glob("*.json")) if p.is_dir() else [p]
    runs = []
    for f in files:
        try:
            runs.append(json.loads(f.read_text()))
        except (OSError, ValueError):
            pass
    runs = [r for r in runs if isinstance(r.get("metrics"), dict)]
    if not runs:
        return None
    metrics: dict = {}
    samples: dict[str, list[float]] = {}
    for k in runs[0]["metrics"]:
        vs = [r["metrics"][k] for r in runs if k in r["metrics"]]
        metrics[k] = vs[0] if is_count(k) else statistics.median(vs)
        for r in runs:
            if k in r["metrics"]:
                samples.setdefault(k, []).extend(
                    r.get("samples", {}).get(k) or [r["metrics"][k]]
                )
    return metrics, samples


def is_count(name: str) -> bool:
    return name.endswith(("_entries", "_lines"))


def fmt(v: object) -> str:
    return f"{v:.2f}" if isinstance(v, float) else str(v)


def compare(
    base: dict | None,
    head: dict,
    base_samples: dict | None = None,
    head_samples: dict | None = None,
) -> tuple[list[str], list[str]]:
    """The table's lines and the warnings."""
    if base is None:
        rows = [
            "### aegis bench",
            "",
            "No base result to compare against.",
            "",
            "| metric | head |",
            "|---|---:|",
        ]
        return rows + [f"| {k} | {fmt(v)} |" for k, v in head.items()], []
    rows = [
        "### aegis bench",
        "",
        "| metric | base | head | change |",
        "|---|---:|---:|---:|",
    ]
    warnings = []
    for k, h in head.items():
        b = base.get(k)
        if b is None or is_count(k) or not b:
            rows.append(f"| {k} | {fmt(b) if b is not None else '–'} | {fmt(h)} | |")
            continue
        change = (h - b) / b
        floor = KB_FLOOR if k.endswith("_kb") else 0.0
        worse = (
            change > WORSE
            and h - b > floor
            and min((head_samples or {}).get(k) or [h])
            > max((base_samples or {}).get(k) or [b])
        )
        rows.append(
            f"| {k} | {fmt(b)} | {fmt(h)} | {change:+.0%}{' ⚠' if worse else ''} |"
        )
        if worse:
            warnings.append(
                f"::warning title=aegis bench::{k} is {change:.0%} worse ({fmt(b)} → {fmt(h)})"
            )
    return rows, warnings


def main() -> int:
    head = load(sys.argv[2])
    if head is None:
        print(f"no bench result at {sys.argv[2]}")
        return 0
    base = load(sys.argv[1])
    rows, warnings = compare(base and base[0], head[0], base and base[1], head[1])
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write("\n".join(rows) + "\n")
    else:
        print("\n".join(rows))
    for w in warnings:
        print(w)
    return 0


if __name__ == "__main__":
    sys.exit(main())
