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


def literal(pattern: str) -> str:
    """The pattern as plain words: each one quoted, so FTS5 ANDs them and
    punctuation such as the dot in notes.md is just text."""
    return " ".join('"' + w.replace('"', '""') + '"' for w in pattern.split())


def search(con: sqlite3.Connection, q: db.Query):
    """db.search, but a pattern FTS5 cannot parse is retried once as literal
    words (q.pattern becomes that form, so a later db.counts agrees). Deliberate
    FTS5 syntax parses the first time and is untouched. BadPattern if neither
    form parses."""
    try:
        return db.search(con, q)
    except db.BadPattern:
        q.pattern = literal(q.pattern)
        return db.search(con, q)
