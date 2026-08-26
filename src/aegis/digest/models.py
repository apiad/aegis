"""The digest's data, frozen and pure."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommitLine:
    """One commit, as `git log --oneline` gives it."""
    sha: str
    subject: str


@dataclass(frozen=True)
class RepoDelta:
    """What one turn did to one repo.

    ``available`` is False for an off-host repo. The same path names a
    different tree on another machine, so probing it locally returns a
    silently wrong answer rather than an error — the rule ``Claim.host``
    and ``render_shared.file_target`` already follow.
    """
    name: str
    host: str = "local"
    commits: tuple[CommitLine, ...] = ()
    files_written: int = 0
    available: bool = True


@dataclass(frozen=True)
class TurnFacts:
    """One turn's substrate movement."""
    repos: tuple[RepoDelta, ...] = ()
    plan_done_delta: int = 0
    plan_done: int = 0
    plan_total: int = 0
    assistant_tail: str = ""
    duration_s: float = 0.0
    error: str = ""

    @property
    def moved(self) -> bool:
        """Did the substrate change?

        The recap's gate. An errored digest is NOT movement: we did not
        observe stillness, we failed to look, and firing on that would
        make every broken collection produce a recap.
        """
        if self.error:
            return False
        if self.plan_done_delta > 0:
            return True
        return any(r.commits or r.files_written for r in self.repos)
