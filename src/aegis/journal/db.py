"""The journal's SQLite file.

Three tables: entries, the paths each entry touched (named by paths.full), and
every handle a session has had, so a search by an old handle finds the session.
An entry is keyed by (log_id, rec, n): the live path and a backfill insert the
same keys, and an insert of a key already there does nothing. A file whose
user_version is not SCHEMA_VERSION is dropped and recreated; the caller then
backfills it.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA_VERSION = 1
TABLES = ("entries_fts", "touches", "handles", "entries")
SCHEMA = """
CREATE TABLE entries(
  id INTEGER PRIMARY KEY, log_id TEXT NOT NULL, rec INTEGER NOT NULL, n INTEGER NOT NULL,
  ts REAL NOT NULL, handle TEXT NOT NULL, repo TEXT, kind TEXT NOT NULL,
  tag TEXT NOT NULL DEFAULT '', text TEXT NOT NULL, source TEXT,
  files_unknown INTEGER NOT NULL DEFAULT 0, UNIQUE(log_id, rec, n));
CREATE INDEX entries_ts ON entries(ts);
CREATE TABLE touches(
  entry_id INTEGER NOT NULL, op TEXT NOT NULL, repo TEXT, path TEXT NOT NULL,
  full TEXT NOT NULL, UNIQUE(entry_id, op, full));
CREATE INDEX touches_full ON touches(full);
CREATE TABLE handles(handle TEXT NOT NULL, log_id TEXT NOT NULL, PRIMARY KEY(handle, log_id));
CREATE VIRTUAL TABLE entries_fts USING fts5(text, content='entries', content_rowid='id');
"""


class BadPattern(ValueError):
    """The search pattern is not FTS5."""


@dataclass
class Entry:
    log_id: str
    rec: int
    n: int
    ts: float
    handle: str
    repo: str | None
    kind: str
    tag: str
    text: str
    source: str | None
    files_unknown: bool
    touches: list[tuple[str, str | None, str, str]]


@dataclass
class Query:
    since: float | None = None
    until: float | None = None
    pattern: str = ""
    path: str = ""
    log_ids: list[str] | None = None
    repo: str = ""
    kinds: list[str] | None = None
    limit: int = 50
    offset: int = 0


@dataclass
class Hit:
    id: int
    ts: float
    log_id: str
    handle: str
    kind: str
    tag: str
    text: str
    source: str | None
    repo: str | None
    files_unknown: bool
    paths: list[str] = field(default_factory=list)


def connect(path: Path) -> tuple[sqlite3.Connection, bool]:
    con = sqlite3.connect(
        path, timeout=10, isolation_level=None, check_same_thread=False
    )
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=10000")
    fresh = con.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION
    if fresh:
        con.execute("BEGIN")
        for t in TABLES:
            con.execute(f"DROP TABLE IF EXISTS {t}")
        for stmt in SCHEMA.split(";"):
            if stmt.strip():
                con.execute(stmt)
        con.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        con.execute("COMMIT")
    return con, fresh


def open_read(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(
        f"file:{path}?mode=ro", uri=True, timeout=10, check_same_thread=False
    )
    con.execute("PRAGMA busy_timeout=10000")
    return con


def clear(con: sqlite3.Connection) -> None:
    con.execute("BEGIN")
    con.execute("INSERT INTO entries_fts(entries_fts) VALUES('delete-all')")
    for t in ("touches", "handles", "entries"):
        con.execute(f"DELETE FROM {t}")
    con.execute("COMMIT")


def insert(con: sqlite3.Connection, e: Entry) -> bool:
    con.execute("BEGIN")
    try:
        row = con.execute(
            "INSERT INTO entries(log_id, rec, n, ts, handle, repo, kind, tag, text, source,"
            " files_unknown) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING RETURNING id",
            (
                e.log_id,
                e.rec,
                e.n,
                e.ts,
                e.handle,
                e.repo,
                e.kind,
                e.tag,
                e.text,
                e.source,
                int(e.files_unknown),
            ),
        ).fetchone()
        if row:
            con.execute(
                "INSERT INTO entries_fts(rowid, text) VALUES(?, ?)", (row[0], e.text)
            )
            con.executemany(
                "INSERT OR IGNORE INTO touches VALUES(?,?,?,?,?)",
                [(row[0], op, repo, path, full) for op, repo, path, full in e.touches],
            )
        if e.handle:
            con.execute(
                "INSERT OR IGNORE INTO handles VALUES(?, ?)", (e.handle, e.log_id)
            )
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    return row is not None


def _like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _where(q: Query) -> tuple[str, list]:
    where: list[str] = []
    args: list = []
    if q.since is not None:
        where.append("e.ts >= ?")
        args.append(q.since)
    if q.until is not None:
        where.append("e.ts < ?")
        args.append(q.until)
    if q.kinds:
        where.append(f"e.kind IN ({','.join('?' * len(q.kinds))})")
        args += q.kinds
    if q.log_ids is not None:
        where.append(f"e.log_id IN ({','.join('?' * len(q.log_ids))})")
        args += q.log_ids
    if q.repo:
        where.append("e.repo = ?")
        args.append(q.repo)
    if q.path:
        base = q.path.rstrip("/") or "/"
        where.append(
            "EXISTS (SELECT 1 FROM touches t WHERE t.entry_id = e.id"
            " AND (t.full = ? OR t.full LIKE ? ESCAPE '\\'))"
        )
        args += [base, _like(base.rstrip("/")) + "/%"]
    if q.pattern:
        where.append(
            "e.id IN (SELECT rowid FROM entries_fts WHERE entries_fts MATCH ?)"
        )
        args.append(q.pattern)
    return (" WHERE " + " AND ".join(where)) if where else "", args


def _run(con: sqlite3.Connection, q: Query, sql: str, args: list) -> list:
    try:
        return con.execute(sql, args).fetchall()
    except sqlite3.OperationalError as err:
        if q.pattern:
            raise BadPattern(str(err)) from err
        raise


def search(con: sqlite3.Connection, q: Query) -> tuple[list[Hit], bool]:
    where, args = _where(q)
    rows = _run(
        con,
        q,
        "SELECT id, ts, log_id, handle, kind, tag, text, source, repo, files_unknown"
        f" FROM entries e{where} ORDER BY e.ts DESC, e.id DESC LIMIT ? OFFSET ?",
        [*args, q.limit + 1, q.offset],
    )
    cut = len(rows) > q.limit
    hits = [Hit(*r[:9], bool(r[9])) for r in rows[: q.limit]]
    if hits:
        by = {h.id: h for h in hits}
        marks = ",".join("?" * len(hits))
        for eid, full in con.execute(
            f"SELECT entry_id, full FROM touches WHERE entry_id IN ({marks}) ORDER BY full",
            list(by),
        ):
            if full not in by[eid].paths:
                by[eid].paths.append(full)
    return hits, cut


def counts(con: sqlite3.Connection, q: Query) -> dict[str, int]:
    where, args = _where(q)
    rows = _run(
        con, q, f"SELECT kind, count(*) FROM entries e{where} GROUP BY kind", args
    )
    return {k: n for k, n in rows}


def log_ids(con: sqlite3.Connection, who: str) -> list[str]:
    found = {
        r[0] for r in con.execute("SELECT log_id FROM handles WHERE handle = ?", (who,))
    }
    return sorted(found | {who})
