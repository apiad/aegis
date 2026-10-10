"""Search parameters to a db.Query: days to bounds, a path or a repo to the
names the index stores, a session to every log id it has had. Shared by the
operations and the CLI so both answer the same."""

from __future__ import annotations

import dataclasses
import os
import sqlite3
from collections.abc import Iterator

from . import db, fuzzy, paths, when
from .render import shown

BATCH = 500


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
    """db.search, but a pattern FTS5 will not run is retried once as literal
    words (q.pattern becomes that form, so a later db.counts agrees). Deliberate
    FTS5 syntax runs the first time and is untouched. Lock contention is not a
    pattern problem and is re-raised. db.BadPattern if the literal form fails too."""
    try:
        return db.search(con, q)
    except (db.BadPattern, sqlite3.OperationalError) as err:
        msg = str(err).lower()
        if not q.pattern or "locked" in msg or "busy" in msg:
            raise
    q.pattern = literal(q.pattern)
    try:
        return db.search(con, q)
    except sqlite3.OperationalError as err:
        if "locked" in str(err).lower() or "busy" in str(err).lower():
            raise
        raise db.BadPattern(str(err)) from err


def matching(
    con: sqlite3.Connection, q: db.Query, text: str, root: str
) -> Iterator[tuple[db.Hit, list[int]]]:
    """The entries q selects whose fields all of text's words fuzzy-match
    (fuzzy.py), newest first, each with the indices in its text that matched.
    SQL narrows by q; the words are matched here, BATCH entries at a time, so a
    caller that stops early reads no further. A field is the text, every handle
    the session has had, a touched path as shown, the kind or the tag."""
    words = text.split()
    names: dict[str, list[str]] = {}
    q = dataclasses.replace(q, limit=BATCH, offset=0, before=None)
    while True:
        hits, cut = search(con, q)
        if new := {h.log_id for h in hits} - names.keys():
            names.update(db.handles(con, new))
        for h in hits:
            fields = [h.handle, *names[h.log_id], h.kind, h.tag]
            fields += [shown(p, root) for p in h.paths]
            marks = fuzzy.match(words, h.text, fields)
            if marks is not None:
                yield h, marks
        if not cut:
            return
        q.before = (hits[-1].ts, hits[-1].id)


def page(
    found: Iterator[tuple[db.Hit, list[int]]], offset: int, limit: int, counts: bool
) -> tuple[list[db.Hit], bool, dict[str, int] | None, list[list[int]]]:
    """Matches offset..offset+limit of ``found``, whether more match, the
    matches by kind when ``counts``, and each hit's marks. It reads one match
    past the page to know there are more; counting reads every candidate the
    SQL filters leave, which is O(n) in the journal and acceptable at its sizes
    (0.16 to 0.22 s over 20,000 entries, measured for #289)."""
    hits: list[db.Hit] = []
    marks: list[list[int]] = []
    by: dict[str, int] = {}
    more = False
    for n, (h, m) in enumerate(found):
        if counts:
            by[h.kind] = by.get(h.kind, 0) + 1
        if n < offset:
            continue
        if n < offset + limit:
            hits.append(h)
            marks.append(m)
            continue
        more = True
        if not counts:
            break
    return hits, more, by if counts else None, marks
