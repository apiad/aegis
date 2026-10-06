"""Compare two scripts/bench2.py results. Reports; never fails.

    uv run python scripts/bench2_compare.py BASE.json HEAD.json

Writes a Markdown table to $GITHUB_STEP_SUMMARY when it is set, else to
stdout, and prints a GitHub ``::warning::`` line on stdout for every metric
more than 20% worse. Every measured metric is lower-is-better; counts
(``*_entries``, ``*_lines``) are context. A missing BASE prints HEAD alone.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

WORSE = 0.20


def load(path: str) -> dict | None:
    try:
        return json.loads(Path(path).read_text())["metrics"]
    except (OSError, ValueError, KeyError):
        return None


def is_count(name: str) -> bool:
    return name.endswith(("_entries", "_lines"))


def fmt(v: object) -> str:
    return f"{v:.2f}" if isinstance(v, float) else str(v)


def compare(base: dict | None, head: dict) -> tuple[list[str], list[str]]:
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
        rows.append(
            f"| {k} | {fmt(b)} | {fmt(h)} | {change:+.0%}{' ⚠' if change > WORSE else ''} |"
        )
        if change > WORSE:
            warnings.append(
                f"::warning title=aegis bench::{k} is {change:.0%} worse ({fmt(b)} → {fmt(h)})"
            )
    return rows, warnings


def main() -> int:
    head = load(sys.argv[2])
    if head is None:
        print(f"no bench result at {sys.argv[2]}")
        return 0
    rows, warnings = compare(load(sys.argv[1]), head)
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
