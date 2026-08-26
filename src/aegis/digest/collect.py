"""Reading what a turn did off git. The impure half.

**Why the base HEAD is captured lazily.** At turn start we do not know
which repos a turn will touch. The obvious alternative — ``git log
--since=<turn start>`` — misattributes a peer's commit in a shared
checkout, and this workspace is one. So the base is read the first time a
write to that repo is *recorded*, which is precise and costs one
``rev-parse``.

Known limitation, stated rather than hidden: a turn that commits without
using a write tool (pure Bash) contributes no commits, because
``repos.writes.write_target`` deliberately excludes Bash.
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from aegis.digest.models import CommitLine, RepoDelta, TurnFacts

MAX_COMMITS = 50
_TIMEOUT_S = 5


def _git(root: Path, *args: str) -> str:
    """Run git, or return "". Never raises — this runs inside a turn."""
    try:
        proc = subprocess.run(
            ("git", *args), cwd=root, capture_output=True, text=True,
            timeout=_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def read_head(root: Path) -> str:
    """The current commit sha, or "" if this is not a readable repo."""
    return _git(Path(root), "rev-parse", "HEAD")


def commits_since(root: Path, base: str, *,
                  max_count: int = MAX_COMMITS) -> tuple[CommitLine, ...]:
    """Commits landed since ``base``, newest first. () on any failure."""
    if not base:
        return ()
    out = _git(Path(root), "log", "--oneline", "--no-decorate",
               f"--max-count={max_count}", f"{base}..HEAD")
    if not out:
        return ()
    lines = []
    for raw in out.splitlines():
        sha, _, subject = raw.partition(" ")
        if sha:
            lines.append(CommitLine(sha=sha, subject=subject.strip()))
    return tuple(lines)


class _Tracked:
    __slots__ = ("root", "host", "base", "writes")

    def __init__(self, root: Path, host: str) -> None:
        self.root = root
        self.host = host
        # Off-host: never resolve against the local disk.
        self.base = read_head(root) if host == "local" else ""
        self.writes = 0


class DigestCollector:
    """Per-session, reset at each turn start."""

    def __init__(self) -> None:
        self._tracked: dict[tuple[str, str], _Tracked] = {}

    def reset(self) -> None:
        self._tracked.clear()

    def note_write(self, root: Path | str, host: str = "local") -> None:
        """Record a write, capturing the repo's base HEAD the first time."""
        root = Path(root)
        key = (host, str(root))
        entry = self._tracked.get(key)
        if entry is None:
            entry = self._tracked[key] = _Tracked(root, host)
        entry.writes += 1

    async def build(self, *, plan_done: int, plan_total: int,
                    plan_done_at_start: int, assistant_tail: str,
                    duration_s: float) -> TurnFacts:
        """Diff every tracked repo. Git runs off the event loop."""
        try:
            deltas = await asyncio.to_thread(self._diff_all)
        except Exception as e:                                # noqa: BLE001
            return TurnFacts(assistant_tail=assistant_tail,
                             duration_s=duration_s,
                             error=f"{type(e).__name__}: {e}")
        return TurnFacts(
            repos=deltas,
            plan_done=plan_done,
            plan_total=plan_total,
            plan_done_delta=max(0, plan_done - plan_done_at_start),
            assistant_tail=assistant_tail,
            duration_s=duration_s)

    def _diff_all(self) -> tuple[RepoDelta, ...]:
        out = []
        for entry in self._tracked.values():
            local = entry.host == "local"
            out.append(RepoDelta(
                name=entry.root.name,
                host=entry.host,
                commits=(commits_since(entry.root, entry.base)
                         if local else ()),
                files_written=entry.writes,
                available=local))
        return tuple(out)
