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


def _git(args: list[str], cwd: str) -> str | None:
    try:
        r = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def _git_binary(args: list[str], cwd: str) -> bytes | None:
    """Run git and return binary output (for -z handling)."""
    try:
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, timeout=10)
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


_checkout_cache: dict[str, tuple[str, str]] = {}


def _checkout(directory: str) -> tuple[str, str] | None:
    """(this worktree's top level, the main checkout) for a directory in a repo."""
    if directory in _checkout_cache:
        return _checkout_cache[directory]
    out = _git(["rev-parse", "--show-toplevel", "--git-common-dir"], directory)
    if not out:
        return None
    top, common = out.splitlines()[:2]
    common = os.path.realpath(os.path.join(directory, common))
    main = os.path.dirname(common) if os.path.basename(common) == ".git" else common
    result = os.path.realpath(top), main
    _checkout_cache[directory] = result
    return result


def name(path: str) -> tuple[str | None, str]:
    try:
        path = os.path.realpath(path)
    except ValueError:
        # NUL byte or other invalid input
        return None, path
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
    out = _git_binary(
        [
            "show",
            "-z",
            "--name-only",
            "--format=",
            "--end-of-options",
            f"{commit}^{{commit}}",
        ],
        _existing(os.path.realpath(directory)),
    )
    if out is None:
        return None
    # Split on null bytes and decode UTF-8, dropping empty strings
    return [name.decode("utf-8") for name in out.split(b"\0") if name]
