"""Name files the way the journal stores them.

A touch is named by its repo's main checkout and its path inside the repo, so an
edit made in a worktree and one made in the main checkout are the same file to
the journal. A path in no repo keeps its absolute real path. Claims do not use
this module: they match real paths, which is what exempts worktrees from them.

Everything here runs git, so it runs on the journal's writer thread. git runs
as ``user``, the Linux user of the session whose turn it reads (launch.py):
git refuses a repository another user owns.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Iterable

from .. import launch


def _git(args: list[str], cwd: str, user: str | None = None) -> str | None:
    try:
        r = launch.run(["git", *args], cwd=cwd, text=True, timeout=10, user=user)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def _git_binary(args: list[str], cwd: str, user: str | None = None) -> bytes | None:
    """Run git and return binary output (for -z handling)."""
    try:
        r = launch.run(["git", *args], cwd=cwd, timeout=10, user=user)
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


_checkout_cache: dict[tuple[str, str | None], tuple[str, str]] = {}


def _checkout(directory: str, user: str | None = None) -> tuple[str, str] | None:
    """(this worktree's top level, the main checkout) for a directory in a repo."""
    if (directory, user) in _checkout_cache:
        return _checkout_cache[(directory, user)]
    out = _git(["rev-parse", "--show-toplevel", "--git-common-dir"], directory, user)
    if not out:
        return None
    top, common = out.splitlines()[:2]
    common = os.path.realpath(os.path.join(directory, common))
    main = os.path.dirname(common) if os.path.basename(common) == ".git" else common
    result = os.path.realpath(top), main
    _checkout_cache[(directory, user)] = result
    return result


def place(path: str, user: str | None = None) -> tuple[str | None, str, str | None]:
    """(the main checkout, the path inside the worktree, the worktree's top
    level) for a path in a repo; (None, the real path, None) outside one."""
    try:
        path = os.path.realpath(path)
    except ValueError:
        # NUL byte or other invalid input
        return None, path, None
    if user is not None:
        return _place_as(path, user)
    hit = _checkout(_existing(path), user)
    if hit is None:
        return None, path, None
    top, main = hit
    rel = os.path.relpath(path, top)
    if rel.startswith(".."):
        return None, path, None
    # A file whose directory is gone was in a tree since removed (a worktree):
    # the nearest existing ancestor's checkout is not where it was written.
    if not os.path.isdir(os.path.dirname(path)):
        return main, "" if rel == "." else rel, None
    return main, "" if rel == "." else rel, top


def _place_as(path: str, user: str) -> tuple[str | None, str, str | None]:
    """``place`` for a path the server may not be able to stat, inside a
    member's home: git, run as ``user``, says where the directory's checkout
    is, and fails when the directory is gone. The path itself is tried first,
    for a directory, then its parent."""
    for d in (path, os.path.dirname(path)):
        hit = _checkout(d, user)
        if hit is None:
            continue
        top, main = hit
        rel = os.path.relpath(path, top)
        if rel.startswith(".."):
            return None, path, None
        return main, "" if rel == "." else rel, top
    return None, path, None


def name(path: str, user: str | None = None) -> tuple[str | None, str]:
    main, rel, _ = place(path, user)
    return main, rel


def full(repo: str | None, rel: str) -> str:
    if repo is None:
        return rel
    return os.path.join(repo, rel) if rel else repo


def _where(directory: str, user: str | None) -> str:
    """Where git runs for ``directory``: its nearest existing ancestor when
    the server looks, the directory itself when a user's git does, since the
    server may not see inside that user's home."""
    real = os.path.realpath(directory)
    return real if user is not None else _existing(real)


def full_sha(directory: str, commit: str, user: str | None = None) -> str | None:
    out = _git(
        ["rev-parse", "--verify", "--end-of-options", f"{commit}^{{commit}}"],
        _where(directory, user),
        user,
    )
    return out.strip() if out else None


def commit_files(
    directory: str, commit: str, user: str | None = None
) -> list[str] | None:
    out = _git_binary(
        [
            "show",
            "-z",
            "--name-only",
            "--format=",
            "--end-of-options",
            f"{commit}^{{commit}}",
        ],
        _where(directory, user),
        user,
    )
    if out is None:
        return None
    # Split on null bytes and decode UTF-8, dropping empty strings
    return [name.decode("utf-8") for name in out.split(b"\0") if name]


def commit_by_subject(
    directory: str, subject: str, ts: float, user: str | None = None
) -> str | None:
    """The full hash of the newest commit on any ref with exactly this subject,
    made by ``ts`` plus a minute for the clock; for a commit that printed no hash.
    None for one no ref holds any more, such as a deleted branch's."""
    out = _git(
        [
            "log",
            "--all",
            "--format=%H%x00%s",
            "--fixed-strings",
            f"--grep={subject}",
            f"--until={int(ts) + 60}",
        ],
        _where(directory, user),
        user,
    )
    for line in (out or "").splitlines():
        h, _, s = line.partition("\0")
        if s == subject:
            return h
    return None


# The owner/name at the end of a remote URL, SSH or HTTPS, without .git.
SLUG = re.compile(r"[:/]([^/:\s]+)/([^/:\s]+?)(?:\.git)?/?$")
_slug_cache: dict[tuple[str, str | None], str | None] = {}


def remote_slug(directory: str, user: str | None = None) -> str | None:
    """The owner/name of a checkout's origin, or None when it has none."""
    if (directory, user) in _slug_cache:
        return _slug_cache[(directory, user)]
    out = _git(["remote", "get-url", "origin"], directory, user)
    m = SLUG.search(out.strip()) if out else None
    slug = f"{m[1]}/{m[2]}" if m else None
    _slug_cache[(directory, user)] = slug
    return slug


def checkout_of(slug: str, tops: Iterable[str], user: str | None) -> str | None:
    """The first of the tops whose origin is the owner/name slug, any case."""
    want = slug.lower()
    for top in tops:
        got = remote_slug(top, user)
        if got and got.lower() == want:
            return top
    return None
