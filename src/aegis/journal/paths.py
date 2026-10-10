"""Name files the way the journal stores them.

A touch is named by its repo's main checkout and its path inside the repo, so an
edit made in a worktree and one made in the main checkout are the same file to
the journal. A path in no repo keeps its absolute real path. Claims do not use
this module: they match real paths, which is what exempts worktrees from them.

Everything here runs git, so it runs on the journal's writer thread.
"""

from __future__ import annotations

import os
import subprocess
from functools import lru_cache


def _git(args: list[str], cwd: str) -> str | None:
    try:
        r = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def _existing(path: str) -> str:
    """The path, or its nearest ancestor that exists."""
    d = path
    while d and not os.path.isdir(d):
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return d


@lru_cache(maxsize=4096)
def _checkout(directory: str) -> tuple[str, str] | None:
    """(this worktree's top level, the main checkout) for a directory in a repo."""
    out = _git(["rev-parse", "--show-toplevel", "--git-common-dir"], directory)
    if not out:
        return None
    top, common = out.splitlines()[:2]
    common = os.path.realpath(os.path.join(directory, common))
    main = os.path.dirname(common) if os.path.basename(common) == ".git" else common
    return os.path.realpath(top), main


def name(path: str) -> tuple[str | None, str]:
    path = os.path.realpath(path)
    hit = _checkout(_existing(path))
    if hit is None:
        return None, path
    top, main = hit
    rel = os.path.relpath(path, top)
    if rel.startswith(".."):
        return None, path
    return main, "" if rel == "." else rel


def full(repo: str | None, rel: str) -> str:
    if repo is None:
        return rel
    return os.path.join(repo, rel) if rel else repo


def commit_files(directory: str, commit: str) -> list[str] | None:
    out = _git(
        ["show", "--name-only", "--format=", commit],
        _existing(os.path.realpath(directory)),
    )
    if out is None:
        return None
    return [line for line in out.splitlines() if line]
