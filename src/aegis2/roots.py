"""The three roots every path resolves against.

The CLI builds them once from the directory it was started in. Everything else
takes them as arguments, so nothing below the CLI depends on the process's
working directory (tests/aegis2/test_no_cwd.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

CONFIG_FILE = ".aegis.yaml"


@dataclass(frozen=True)
class Roots:
    config_root: Path
    state_root: Path
    harness_cwd: Path


def find_config_root(start: Path) -> Path:
    """The nearest ancestor of ``start`` holding ``.aegis.yaml``, else ``start``."""
    start = start.resolve()
    for d in (start, *start.parents):
        if (d / CONFIG_FILE).is_file():
            return d
    return start


def make_roots(start: Path, root: Path | None) -> Roots:
    config_root = root.resolve() if root is not None else find_config_root(start)
    return Roots(
        config_root=config_root,
        state_root=config_root / ".aegis2" / "state",
        harness_cwd=config_root,
    )
