"""git history for one principal's knowledge directory.

Every change through aegis is one commit, so a merge or a delete in
/consolidate can be undone. git runs with the host's own configuration shut
out (``GIT_CONFIG_GLOBAL=/dev/null``, no system config) and a fixed committer,
so a signing hook or a template in someone's ``~/.gitconfig`` never decides
whether a save lands; every ``GIT_*`` variable the server inherited is dropped
for the same reason. Who made the change goes in a ``Session:`` trailer.
Nothing is ever pushed.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .. import launch

_ENV = {
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_TERMINAL_PROMPT": "0",
}
_ID = ["-c", "user.name=aegis", "-c", "user.email=aegis@localhost"]


def _env() -> dict[str, str]:
    """The server's environment with every inherited ``GIT_*`` variable
    dropped (an author, an index file, a work tree set for something else)
    and ours put in."""
    keep = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return {**keep, **_ENV}


def _git(
    root: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    # The server's own user: the store lives in the state directory.
    argv = ["git", "-C", str(root), *_ID, *args]
    r = launch.run(argv, user=None, env=_env(), text=True)
    if check and r.returncode != 0:
        raise subprocess.CalledProcessError(r.returncode, argv, r.stdout, r.stderr)
    return r


def ensure_repo(root: Path) -> None:
    """Make ``root`` a git repository, once."""
    root.mkdir(parents=True, exist_ok=True)
    if not (root / ".git").exists():
        _git(root, "init", "-q", "-b", "main")


def commit(root: Path, paths: list[str], message: str, author: str) -> str:
    """Commit ``paths`` (written, changed or deleted); the new sha, or "" when
    none of them changed."""
    for p in paths:
        if (root / p).exists():
            _git(root, "add", "-A", "--", p)
        else:  # gone, and maybe never tracked: a plain add would refuse it
            _git(root, "rm", "-r", "-q", "--cached", "--ignore-unmatch", "--", p)
    if _git(root, "diff", "--cached", "--quiet", check=False).returncode == 0:
        return ""
    _git(root, "commit", "-q", "--no-verify", "-m", f"{message}\n\nSession: {author}")
    return _git(root, "rev-parse", "HEAD").stdout.strip()


def unstage(root: Path, paths: list[str]) -> None:
    """Take ``paths`` out of the index again, after a commit that failed;
    best effort, since git itself may be what failed."""
    try:
        _git(root, "reset", "-q", "--", *paths, check=False)
    except OSError:
        pass


def log(root: Path, relpath: str, limit: int = 50) -> list[dict]:
    """The commits that touched ``relpath``, newest first: sha, at (epoch
    seconds), the subject and ``by``, the commit's ``Session:`` trailer."""
    fmt = (
        "--format=%H%x00%ct%x00%s%x00"
        "%(trailers:key=Session,valueonly,separator=%x2C)%x1e"
    )
    out = _git(root, "log", f"-{limit}", fmt, "--", relpath, check=False)
    rows = []
    for record in out.stdout.split("\x1e"):
        if not record.strip():
            continue
        sha, at, message, by = record.strip("\n").split("\0", 3)
        rows.append({"sha": sha, "at": int(at), "message": message, "by": by.strip()})
    return rows


def show(root: Path, relpath: str, sha: str) -> str | None:
    """``relpath`` as it was at ``sha``; None when it did not exist then."""
    r = _git(root, "show", f"{sha}:{relpath}", check=False)
    return r.stdout if r.returncode == 0 else None


def restore(root: Path, relpath: str, sha: str, message: str, author: str) -> str:
    """Put ``relpath`` (a file or a folder) back as it was at ``sha`` and
    commit; the new sha, "" when it already was. A folder loses the tracked
    files ``sha`` did not have."""
    _git(root, "rm", "-r", "-f", "-q", "--ignore-unmatch", "--", relpath)
    _git(root, "checkout", sha, "--", relpath)
    return commit(root, [relpath], message, author)


def revert(root: Path, sha: str, author: str) -> str:
    """Undo the commit ``sha`` with a new commit; the new sha. Only the paths
    ``sha`` touched are committed, so a hand edit to another file stays out
    of it. A revert that fails (a conflict with a later change) is aborted,
    so the work tree is left as it was, and the error raised."""
    touched = _git(
        root, "diff-tree", "--no-commit-id", "--name-only", "-r", sha
    ).stdout.splitlines()
    try:
        _git(root, "revert", "--no-edit", "--no-commit", sha)
    except subprocess.CalledProcessError:
        _git(root, "revert", "--abort", check=False)
        raise
    return commit(root, touched, f"revert {sha[:12]}", author)
