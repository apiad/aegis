"""The three roots every path resolves against.

The CLI builds them once from the directory it was started in. Everything else
takes them as arguments, so nothing below the CLI depends on the process's
working directory (tests/test_no_cwd.py).
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


# Files only the legacy tree wrote in its state directory. Finding one means the
# directory holds that tree's state, in another format, and aegis must not mix
# its own into it (``aegis serve`` refuses to start; see legacy_state()).
LEGACY_MARKERS = (
    "daemon.lock",
    "daemon.sock",
    "workspace.json",
    "history_index.json",
    "comms",
)


def legacy_state(state_root: Path) -> list[str]:
    """The legacy tree's marker files present in ``state_root``, if any."""
    return [m for m in LEGACY_MARKERS if (state_root / m).exists()]


def make_roots(start: Path, root: Path | None) -> Roots:
    config_root = root.resolve() if root is not None else find_config_root(start)
    return Roots(
        config_root=config_root,
        state_root=config_root / ".aegis" / "state",
        harness_cwd=config_root,
    )
