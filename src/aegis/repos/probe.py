"""Repo root resolution, and one subprocess for everything else.

``git status --porcelain=v2 --branch`` returns branch, upstream, ahead/behind
and the dirty list in a *single* call, so the design question is never which
fields to show but whether to shell out at all.

Every failure here degrades rather than raises. A dashboard section that
takes the paint down with it when a repo is mid-rebase is worse than no
section, and ``git status`` on a large tree is exactly the call that hangs.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from aegis.repos.models import RepoState

log = logging.getLogger(__name__)

PROBE_TIMEOUT = 3.0

# An untracked file larger than this is not counted. Nothing an agent wrote
# by hand is this big, and reading a 400 MB dataset to count its newlines
# would hang the probe for exactly the file the number should ignore.
MAX_UNTRACKED_BYTES = 1_000_000

# Marker → the operation it means, checked in the repo's git dir. porcelain=v2
# does not report an in-progress operation, so this is read off disk.
_OPS = (
    ("rebase-merge", "rebase"),
    ("rebase-apply", "rebase"),
    ("MERGE_HEAD", "merge"),
    ("CHERRY_PICK_HEAD", "cherry-pick"),
    ("BISECT_LOG", "bisect"),
)


@dataclass(frozen=True)
class Baseline:
    """Where this session found a repo — what its churn is measured from.

    Captured once, at the first write, and never re-captured: moving the
    anchor forward would silently discard everything the session had already
    done there.

    ``added``/``deleted``/``untracked`` are the work that was *already*
    there when we arrived. They are subtracted back out so a checkout that
    was dirty before the session started does not read as the session's
    doing — the failure ``~n`` has always had, and the reason this is a
    baseline rather than a bare sha.
    """

    sha: str = ""
    added: int = 0
    deleted: int = 0
    untracked: frozenset[str] = frozenset()


def find_repo_root(path: str | Path) -> Path | None:
    """The nearest ancestor of ``path`` containing ``.git``, or ``None``.

    Walks up rather than shelling out to ``rev-parse``: this runs on every
    write event, and the answer is a filesystem fact. Walking up also gives
    the *nearest* root for free, which is what makes ``repos/aegis`` inside
    a git-tracked workspace resolve to ``aegis`` and not to the workspace.

    ``path`` need not exist — a ``Write`` names a file before there is one.
    """
    try:
        p = Path(path).expanduser().resolve()
    except (OSError, RuntimeError):  # unresolvable symlink loop, bad path
        return None
    for candidate in (p, *p.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def git_dir(root: Path) -> Path:
    """The repo's git directory.

    ``.git`` is a *file* carrying a ``gitdir:`` pointer in a worktree or a
    submodule; a caller that assumes a directory reads no HEAD there and
    reports every worktree as detached.
    """
    dot = root / ".git"
    if dot.is_file():
        try:
            text = dot.read_text(errors="replace").strip()
        except OSError:
            return dot
        if text.startswith("gitdir:"):
            target = Path(text.removeprefix("gitdir:").strip())
            return target if target.is_absolute() else (root / target)
    return dot


def read_head_branch(root: Path) -> str:
    """The current branch from ``.git/HEAD``, with no subprocess.

    Empty when detached, or when HEAD cannot be read. This is the free path:
    it is what a row shows on its first paint, before any probe has landed,
    and what it keeps when ``git`` is not on PATH.
    """
    try:
        head = (git_dir(root) / "HEAD").read_text(errors="replace").strip()
    except OSError:
        return ""
    return head.removeprefix("ref: refs/heads/") if head.startswith(
        "ref: refs/heads/") else ""


def _in_progress_op(root: Path) -> str:
    gd = git_dir(root)
    for marker, name in _OPS:
        if (gd / marker).exists():
            return name
    return ""


def _parse_status_v2(out: str) -> tuple[str, int, int, int, bool]:
    """``(branch, ahead, behind, dirty, detached)`` from porcelain v2."""
    branch, ahead, behind, dirty, detached = "", 0, 0, 0, False
    for line in out.splitlines():
        if not line:
            continue
        if not line.startswith("# "):
            # Every non-header line is one changed path: tracked changes
            # ("1"/"2"), unmerged ("u"), untracked ("?"), ignored ("!" —
            # off by default).
            dirty += 1
            continue
        head, _, rest = line[2:].partition(" ")
        if head == "branch.head":
            # git spells a detached HEAD "(detached)" here, which is not a
            # branch name and must not be rendered as one.
            if rest == "(detached)":
                detached = True
            else:
                branch = rest
        elif head == "branch.ab":
            for tok in rest.split():
                if tok.startswith("+"):
                    ahead = int(tok[1:] or 0)
                elif tok.startswith("-"):
                    behind = int(tok[1:] or 0)
    return branch, ahead, behind, dirty, detached


def _git(root: Path, *args: str) -> str | None:
    """One git call's stdout, or ``None`` on any failure.

    Same contract as the rest of this module: a missing binary, a hung
    ``git``, or a non-zero exit degrades the number, never the section.
    """
    if shutil.which("git") is None:
        return None
    try:
        proc = subprocess.run(["git", "-C", str(root), *args],
                              capture_output=True, text=True,
                              timeout=PROBE_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("git %s failed for %s: %s", args[0], root, exc)
        return None
    if proc.returncode != 0:
        log.debug("git %s rc=%s for %s: %s", args[0], proc.returncode, root,
                  proc.stderr.strip()[:200])
        return None
    return proc.stdout


def _numstat(root: Path, *args: str) -> tuple[int, int]:
    """``(added, deleted)`` from ``git diff --numstat``.

    A binary file reports ``-`` for both counts and contributes nothing,
    which is the right answer: "lines" is not a fact about a PNG.
    """
    added = deleted = 0
    for line in (_git(root, "diff", "--numstat", *args) or "").splitlines():
        a, _, rest = line.partition("\t")
        d, _, _ = rest.partition("\t")
        if a.isdigit() and d.isdigit():
            added += int(a)
            deleted += int(d)
    return added, deleted


def _untracked(root: Path) -> frozenset[str]:
    """Untracked, non-ignored paths, relative to ``root``.

    ``ls-files --others`` rather than the ``?`` lines of the status we
    already ran: status collapses an untracked *directory* into a single
    entry, so a brand-new package would count as one file with no lines.
    """
    out = _git(root, "ls-files", "--others", "--exclude-standard", "-z")
    return frozenset(p for p in (out or "").split("\0") if p)


def _count_lines(path: Path) -> int:
    """Lines in an untracked file, counted the way ``git diff`` counts.

    Zero for anything binary or oversized: nothing an agent wrote by hand
    is a megabyte, and reading a large dataset to count its newlines would
    hang the probe for exactly the file the number should ignore.
    """
    try:
        with path.open("rb") as fh:
            blob = fh.read(MAX_UNTRACKED_BYTES + 1)
    except OSError:
        return 0
    if len(blob) > MAX_UNTRACKED_BYTES or b"\0" in blob:
        return 0
    return blob.count(b"\n") + (0 if not blob or blob.endswith(b"\n") else 1)


def capture_baseline(root: Path) -> Baseline:
    """Read where the session is starting from. Blocking; never raises.

    Called once per repo, on the write *event* — which the harness emits
    before the tool runs — so the first file's changes land on the
    session's side of the line rather than inside the baseline.
    """
    sha = (_git(root, "rev-parse", "HEAD") or "").strip()
    added, deleted = _numstat(root, "HEAD") if sha else (0, 0)
    return Baseline(sha=sha, added=added, deleted=deleted,
                    untracked=_untracked(root))


def _churn(root: Path, baseline: Baseline | None) -> tuple[int, int]:
    """Lines written since ``baseline`` — committed and uncommitted alike.

    ``git diff <sha>`` covers everything tracked: the commits the session
    made and the working tree on top of them. Untracked files are invisible
    to it and get counted by hand, because a session of brand-new files is
    precisely the case this number exists for.
    """
    if baseline is None:
        return 0, 0
    added, deleted = _numstat(root, baseline.sha) if baseline.sha else (0, 0)
    for rel in _untracked(root) - baseline.untracked:
        added += _count_lines(root / rel)
    # Clamped: an agent that reverts work someone else left uncommitted
    # drives the subtraction below zero, and "-3 lines added" is not a fact.
    return max(added - baseline.added, 0), max(deleted - baseline.deleted, 0)


def probe_repo(root: Path, baseline: Baseline | None = None) -> RepoState:
    """One ``git status --porcelain=v2 --branch``, folded into a ``RepoState``.

    Blocking — call it off the UI thread. Never raises: any failure comes
    back as a ``stale`` state carrying whatever the free path could read.

    ``baseline`` adds the session's line churn, at the cost of two more git
    calls; without one the state carries the branch and counts alone.
    """
    fallback = RepoState(root=root, branch=read_head_branch(root),
                         op=_in_progress_op(root), stale=True)
    if shutil.which("git") is None:
        return fallback
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain=v2", "--branch"],
            capture_output=True, text=True, timeout=PROBE_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("repo probe failed for %s: %s", root, exc)
        return fallback
    if proc.returncode != 0:
        log.debug("repo probe rc=%s for %s: %s",
                  proc.returncode, root, proc.stderr.strip()[:200])
        return fallback

    branch, ahead, behind, dirty, detached = _parse_status_v2(proc.stdout)
    added, deleted = _churn(root, baseline)
    return RepoState(root=root, branch=branch, ahead=ahead, behind=behind,
                     dirty=dirty, added=added, deleted=deleted,
                     detached=detached,
                     op=_in_progress_op(root), stale=False)
