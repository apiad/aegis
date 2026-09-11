"""The three roots aegis resolves paths against.

They coincide when aegis runs from a project directory on a CLI, which is
why they were conflated as ``Path.cwd()`` for so long. They do not coincide
when aegis is embedded: one process holds several instances, each rooted at
a different worktree.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AegisRoots:
    """Where this aegis instance resolves things.

    config_root: holds .aegis.yaml, its overlays and plugin dirs.
    state_root:  parent of .aegis/state — persistence, locks, canvas,
                 terminals, views, and per-session hook/digest/recap state.
    harness_cwd: the directory an agent subprocess actually runs in. A
                 relative ``prompt:`` persona resolves here too, not under
                 config_root — the launcher reads it against its local root
                 (config/persona.py, hosts/launcher.py). Identical on a CLI,
                 divergent when embedded.
    """

    config_root: Path
    state_root: Path
    harness_cwd: Path

    @classmethod
    def for_project(cls, root: Path,
                    harness_cwd: Path | None = None) -> "AegisRoots":
        """All three from one project directory, the CLI case."""
        resolved = Path(root).resolve()
        return cls(
            config_root=resolved,
            state_root=resolved,
            harness_cwd=Path(harness_cwd).resolve() if harness_cwd
            else resolved,
        )

    @property
    def state_dir(self) -> Path:
        """Mirrors ``state.workspace.state_dir`` so the two never drift."""
        return self.state_root / ".aegis" / "state"
