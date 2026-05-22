"""Git helpers for review_branch and friends."""
from __future__ import annotations

import subprocess


def diff_vs(base: str = "main") -> str:
    return subprocess.run(
        ["git", "diff", f"{base}...HEAD"],
        capture_output=True, text=True, check=False).stdout


def branch_slug() -> str:
    out = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, check=False).stdout.strip()
    return out.replace("/", "-")
