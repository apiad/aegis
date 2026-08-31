"""What the REPOS section renders."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class RepoState:
    """One repo's git state, as of the last successful probe.

    ``stale`` means the numbers are the last ones we managed to read, not
    the current ones — a probe that timed out, exited non-zero, or never
    ran. Rendering them dim is honest; dropping them would lose a branch
    name we do know, and showing them as fresh would be a lie.
    """

    root: Path
    branch: str = ""
    ahead: int = 0
    behind: int = 0
    dirty: int = 0
    # Lines this aegis session has written here, measured from the baseline
    # captured at its first write. Unlike ``dirty`` these span commits: the
    # question the row answers is how much work happened, and `~n` goes to
    # zero every time an agent commits.
    added: int = 0
    deleted: int = 0
    detached: bool = False
    op: str = ""            # "" | "merge" | "rebase" | "cherry-pick" | "bisect"
    stale: bool = False

    @property
    def name(self) -> str:
        return self.root.name


@dataclass(frozen=True)
class RepoView:
    """A render row: a repo's state plus who has been writing to it.

    ``host`` is the machine the writing session's harness runs on. An
    off-host repo is listed but never probed — the identically-named local
    path is a different file, and ``git status`` against it would return a
    silently wrong answer rather than an error. Same reasoning as
    ``Claim.host`` and ``render_shared.file_target``.
    """

    state: RepoState
    writers: tuple[str, ...] = ()      # live handles, most recent first
    mine: bool = False                 # is the asking pane's agent a writer
    host: str = "local"

    @property
    def shared(self) -> bool:
        """More than one live agent writing here — the collision."""
        return len(self.writers) > 1

    @property
    def others(self) -> tuple[str, ...]:
        return self.writers

    @property
    def label(self) -> str:
        name = self.state.name
        return name if self.host == "local" else f"{name}@{self.host}"


@dataclass
class _Membership:
    """Mutable tracker bookkeeping — not part of the render contract."""

    root: Path
    host: str = "local"
    writers: list[str] = field(default_factory=list)
    last_write: float = 0.0
