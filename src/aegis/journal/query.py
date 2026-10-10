"""Search parameters to a db.Query: days to bounds, a path or a repo to the
names the index stores, a session to every log id it has had. Shared by the
operations and the CLI so both answer the same."""

from __future__ import annotations

import os
import sqlite3

from . import db, paths, when


def _abs(p: str, root: str) -> str:
    return os.path.realpath(os.path.join(root, os.path.expanduser(p)))


def build(
    con: sqlite3.Connection,
    root: str,
    *,
    since=None,
    until=None,
    pattern=None,
    path=None,
    session=None,
    repo=None,
    kinds=None,
    limit=50,
    offset=0,
) -> db.Query:
    q = db.Query(
        limit=limit,
        offset=offset,
        pattern=(pattern or "").strip(),
        kinds=list(kinds) if kinds else None,
    )
    q.since = when.bound(since, end=False)
    q.until = when.bound(until, end=True)
    if path:
        q.path = paths.full(*paths.name(_abs(path, root)))
    if repo:
        q.repo = paths.name(_abs(repo, root))[0] or _abs(repo, root)
    if session:
        q.log_ids = db.log_ids(con, session)
    return q
