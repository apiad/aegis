"""Who owns a repo: the account or organisation in its origin URL (spec 06).

Read once per repo on the journal's writer thread and kept in ``repo_owners``;
read again when the repo's ``.git/config`` is newer than the reading, so a
changed remote is seen. A repo with no remote is ``local`` unless the config's
``usage.owners`` names its owner, by the repo's directory name or its path.
Owner names (``usage.owner_names``) are for display only: page.py applies
them, and nothing here stores them.
"""

from __future__ import annotations

import os
import sqlite3
import time

from ..journal import paths

LOCAL = "local"


def _mtime(repo: str) -> float:
    try:
        return os.stat(os.path.join(repo, ".git", "config")).st_mtime
    except OSError:
        return 0.0


def owner_of(
    con: sqlite3.Connection, repo: str, top: str, user: str | None, config: dict
) -> str:
    """The owner of the repo whose main checkout is ``repo``; ``top`` is the
    worktree git runs in."""
    named = config.get("owners") or {}
    if owner := named.get(os.path.basename(repo.rstrip("/"))) or named.get(repo):
        return owner
    row = con.execute(
        "SELECT owner, checked_at FROM repo_owners WHERE repo = ?", (repo,)
    ).fetchone()
    if row and row[1] >= _mtime(repo):
        return row[0]
    # Not remote_slug: it caches per process, and a changed remote must show.
    out = paths._git(["remote", "get-url", "origin"], top or repo, user)
    m = paths.SLUG.search(out.strip()) if out else None
    remote = f"{m[1]}/{m[2]}" if m else None
    owner = m[1] if m else LOCAL
    con.execute(
        "INSERT INTO repo_owners VALUES(?, ?, ?, ?) ON CONFLICT(repo) DO UPDATE SET"
        " owner = excluded.owner, remote = excluded.remote,"
        " checked_at = excluded.checked_at",
        (repo, owner, remote, time.time()),
    )
    return owner
