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
from pathlib import Path

from aegis.repos.models import RepoState

log = logging.getLogger(__name__)

PROBE_TIMEOUT = 3.0

# Marker → the operation it means, checked in the repo's git dir. porcelain=v2
# does not report an in-progress operation, so this is read off disk.
_OPS = (
    ("rebase-merge", "rebase"),
    ("rebase-apply", "rebase"),
    ("MERGE_HEAD", "merge"),
    ("CHERRY_PICK_HEAD", "cherry-pick"),
    ("BISECT_LOG", "bisect"),
)


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


def probe_repo(root: Path) -> RepoState:
    """One ``git status --porcelain=v2 --branch``, folded into a ``RepoState``.

    Blocking — call it off the UI thread. Never raises: any failure comes
    back as a ``stale`` state carrying whatever the free path could read.
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
    return RepoState(root=root, branch=branch, ahead=ahead, behind=behind,
                     dirty=dirty, detached=detached,
                     op=_in_progress_op(root), stale=False)
