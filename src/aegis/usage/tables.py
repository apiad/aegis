"""The usage tables in journal.db (spec 05).

They live beside the journal's tables but keep their own version and backfill
marker in ``usage_meta``: the journal's ``application_id`` marks the journal's
backfill, so adding these tables to a complete journal backfills usage only.
A row is keyed by the store record that produced it, as the journal's are, and
inserted with ON CONFLICT DO NOTHING, so live and backfill write the same rows.
A ``Settle`` fixes the purpose of a part written before its purpose was known.
Money is REAL dollars rounded to 1e-9: SQLite has no decimal.

A part carries its turn's dimensions (person, kind, harness, model, mode,
billing, cost_kind) and its own calls, errors and exits, so a query reads one
table: the join to the turns doubled a four-week query's time. ``turn`` is
its turn's rowid, so a query counts distinct turns by an integer.
"""

from __future__ import annotations

import sqlite3
from decimal import Decimal

from .turns import Derived

USAGE_VERSION = 2
# Derived from the stores: dropped by a version bump, emptied by a rebuild.
DERIVED = ("usage_turns", "usage_parts", "usage_waits", "usage_ctx", "usage_whatif")
# What the import read (importer.py): rows of an imported session, log id
# cc-<stem>, are not in any store, so a rebuild keeps them and these.
IMPORTS = ("usage_imports", "usage_import_ids")
TABLES = (*DERIVED, *IMPORTS, "usage_quota", "repo_owners", "usage_meta")
SCHEMA = """
CREATE TABLE usage_turns(
  log_id TEXT NOT NULL, rec INTEGER NOT NULL, ts REAL NOT NULL, person TEXT NOT NULL,
  kind TEXT NOT NULL, harness TEXT NOT NULL, model TEXT NOT NULL, mode TEXT NOT NULL,
  billing TEXT NOT NULL, cost REAL NOT NULL, cost_kind TEXT NOT NULL,
  tok_in INTEGER NOT NULL, tok_out INTEGER NOT NULL, tok_cw INTEGER NOT NULL,
  tok_cr INTEGER NOT NULL, secs REAL NOT NULL, calls INTEGER NOT NULL,
  errors INTEGER NOT NULL, exits INTEGER NOT NULL, ctx_start INTEGER NOT NULL,
  ctx_peak INTEGER NOT NULL, continues TEXT, PRIMARY KEY(log_id, rec));
CREATE INDEX usage_turns_ts ON usage_turns(ts);
CREATE TABLE usage_parts(
  log_id TEXT NOT NULL, rec INTEGER NOT NULL, n INTEGER NOT NULL, ts REAL NOT NULL,
  repo TEXT, purpose TEXT NOT NULL, activity TEXT NOT NULL, settled INTEGER NOT NULL,
  cost REAL NOT NULL, tokens INTEGER NOT NULL, secs REAL NOT NULL,
  calls INTEGER NOT NULL, errors INTEGER NOT NULL, exits INTEGER NOT NULL,
  person TEXT NOT NULL, kind TEXT NOT NULL, harness TEXT NOT NULL, model TEXT NOT NULL,
  mode TEXT NOT NULL, billing TEXT NOT NULL, cost_kind TEXT NOT NULL,
  turn INTEGER NOT NULL, PRIMARY KEY(log_id, rec, n));
CREATE INDEX usage_parts_ts ON usage_parts(ts);
CREATE INDEX usage_parts_repo ON usage_parts(repo, ts);
CREATE TABLE usage_waits(
  log_id TEXT NOT NULL, rec INTEGER NOT NULL, ts REAL NOT NULL, woke_by TEXT NOT NULL,
  secs REAL NOT NULL, PRIMARY KEY(log_id, rec));
CREATE INDEX usage_waits_ts ON usage_waits(ts);
CREATE TABLE usage_ctx(
  log_id TEXT NOT NULL, rec INTEGER NOT NULL, band INTEGER NOT NULL,
  calls INTEGER NOT NULL, cost REAL NOT NULL, tok_cr INTEGER NOT NULL,
  PRIMARY KEY(log_id, rec, band));
CREATE TABLE usage_whatif(
  log_id TEXT NOT NULL, rec INTEGER NOT NULL, at INTEGER NOT NULL,
  handoffs INTEGER NOT NULL, saved REAL NOT NULL, rewrite REAL NOT NULL,
  PRIMARY KEY(log_id, rec, at));
CREATE TABLE usage_quota(
  billing TEXT NOT NULL, window TEXT NOT NULL, ts REAL NOT NULL, percent REAL NOT NULL,
  resets_at REAL, PRIMARY KEY(billing, window, ts));
CREATE TABLE repo_owners(
  repo TEXT PRIMARY KEY, owner TEXT NOT NULL, remote TEXT, checked_at REAL NOT NULL);
CREATE TABLE usage_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE usage_imports(path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime REAL NOT NULL);
CREATE TABLE usage_import_ids(mid TEXT PRIMARY KEY, path TEXT NOT NULL);
CREATE INDEX usage_import_ids_path ON usage_import_ids(path);
"""
COMPLETE = "complete"


def _money(x: Decimal) -> float:
    return round(float(x), 9)


def _get(con: sqlite3.Connection, key: str) -> str | None:
    try:
        row = con.execute(
            "SELECT value FROM usage_meta WHERE key = ?", (key,)
        ).fetchone()
    except sqlite3.OperationalError:
        return None  # no usage_meta yet
    return row[0] if row else None


def _set(con: sqlite3.Connection, key: str, value: str) -> None:
    con.execute(
        "INSERT INTO usage_meta VALUES(?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def ensure(con: sqlite3.Connection) -> bool:
    """Create the usage tables, or recreate them when their version is not
    this one's; True when they are fresh and need a backfill. The journal's
    tables are not touched."""
    con.execute("BEGIN IMMEDIATE")
    try:
        fresh = _get(con, "version") != str(USAGE_VERSION)
        if fresh:
            for t in TABLES:
                con.execute(f"DROP TABLE IF EXISTS {t}")
            for stmt in SCHEMA.split(";"):
                if stmt.strip():
                    con.execute(stmt)
            _set(con, "version", str(USAGE_VERSION))
            _set(con, "backfill", "0")
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    return fresh


def clear(con: sqlite3.Connection, token: int) -> None:
    """Empty the derived tables and mark a backfill ``token`` in one
    transaction. Quota readings, repo owners and imported sessions are not
    derived from stores, and Claude Code purges what was imported."""
    con.execute("BEGIN")
    try:
        for t in DERIVED:
            con.execute(f"DELETE FROM {t} WHERE log_id NOT LIKE 'cc-%'")
        _set(con, "backfill", str(token))
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise


def insert(con: sqlite3.Connection, log_id: str, d: Derived) -> int:
    """Write what one record derived, in one transaction; the rows added or
    changed."""
    before = con.total_changes
    con.execute("BEGIN")
    try:
        con.executemany(
            "INSERT INTO usage_turns VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT DO NOTHING",
            [
                (
                    log_id,
                    t.rec,
                    t.ts,
                    t.person,
                    t.kind,
                    t.harness,
                    t.model,
                    t.mode,
                    t.billing,
                    _money(t.cost),
                    t.cost_kind,
                    t.tok_in,
                    t.tok_out,
                    t.tok_cw,
                    t.tok_cr,
                    t.secs,
                    t.calls,
                    t.errors,
                    t.exits,
                    t.ctx_start,
                    t.ctx_peak,
                    t.continues,
                )  # fmt: skip
                for t in d.turns
            ],
        )
        turn = {t.rec: t for t in d.turns}  # a part comes with its turn
        rowid = {
            rec: con.execute(
                "SELECT rowid FROM usage_turns WHERE log_id = ? AND rec = ?",
                (log_id, rec),
            ).fetchone()[0]
            for rec in turn
        }
        con.executemany(
            "INSERT INTO usage_parts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT DO NOTHING",
            [
                (
                    log_id,
                    p.rec,
                    p.n,
                    p.ts,
                    p.repo,
                    p.purpose,
                    p.activity,
                    int(p.settled),
                    _money(p.cost),
                    p.tokens,
                    p.secs,
                    p.calls,
                    p.errors,
                    p.exits,
                    turn[p.rec].person,
                    turn[p.rec].kind,
                    turn[p.rec].harness,
                    turn[p.rec].model,
                    turn[p.rec].mode,
                    turn[p.rec].billing,
                    turn[p.rec].cost_kind,
                    rowid[p.rec],
                )
                for p in d.parts
            ],
        )
        con.executemany(
            "INSERT INTO usage_waits VALUES(?,?,?,?,?) ON CONFLICT DO NOTHING",
            [(log_id, w.rec, w.ts, w.woke_by, w.secs) for w in d.waits],
        )
        con.executemany(
            "INSERT INTO usage_ctx VALUES(?,?,?,?,?,?) ON CONFLICT DO NOTHING",
            [
                (log_id, b.rec, b.band, b.calls, _money(b.cost), b.tok_cr)
                for b in d.bands
            ],
        )
        con.executemany(
            "INSERT INTO usage_whatif VALUES(?,?,?,?,?,?) ON CONFLICT DO NOTHING",
            [
                (log_id, w.rec, w.at, w.handoffs, _money(w.saved), _money(w.rewrite))
                for w in d.whatifs
            ],
        )
        con.executemany(
            "UPDATE usage_parts SET purpose = ?, settled = 1"
            " WHERE log_id = ? AND rec = ? AND n = ? AND settled = 0",
            [(s.purpose, log_id, s.rec, s.n) for s in d.settles],
        )
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    return con.total_changes - before


def mark(con: sqlite3.Connection, token: int) -> None:
    """A usage backfill run ``token`` begins."""
    _set(con, "backfill", str(token))


def complete_if(con: sqlite3.Connection, token: int) -> None:
    """Mark the usage backfill complete if run ``token`` is still the latest:
    a rebuild that cleared meanwhile leaves it incomplete."""
    con.execute("BEGIN IMMEDIATE")
    try:
        if _get(con, "backfill") == str(token):
            _set(con, "backfill", COMPLETE)
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise


def is_complete(con: sqlite3.Connection) -> bool:
    return _get(con, "backfill") == COMPLETE


def quota(con: sqlite3.Connection, snapshot: dict) -> int:
    """Keep each window of a quota snapshot (Quota.snapshot) whose percent or
    reset time changed since the last row for its (billing, window); the rows
    added. A provider with no account hash bills no one and is skipped."""
    rows = []
    for p in snapshot.get("providers") or []:
        if not p.get("account") or p.get("read_at") is None:
            continue
        billing = f"{p['name']}:{p['account']}"
        for w in p.get("windows") or []:
            last = con.execute(
                "SELECT percent, resets_at FROM usage_quota"
                " WHERE billing = ? AND window = ? ORDER BY ts DESC LIMIT 1",
                (billing, w["kind"]),
            ).fetchone()
            now = (float(w["percent"]), w.get("resets_at"))
            if last is None or (last[0], last[1]) != now:
                rows.append((billing, w["kind"], float(p["read_at"]), *now))
    if rows:
        with con:
            con.executemany(
                "INSERT INTO usage_quota VALUES(?,?,?,?,?) ON CONFLICT DO NOTHING",
                rows,
            )
    return len(rows)


def usual_spend(
    con: sqlite3.Connection,
    billing: str,
    window: str,
    span_s: float,
    now: float,
    keep: int,
) -> float | None:
    """The mean percent the last ``keep`` finished windows ended on, or None
    when none has finished. A window's rows share a reset time up to a vendor's
    jitter, and percent only grows within one, so its last value is its highest."""
    rows = con.execute(
        "SELECT resets_at, percent FROM usage_quota WHERE billing = ? AND window = ?"
        " AND resets_at IS NOT NULL AND resets_at <= ? ORDER BY resets_at DESC",
        (billing, window, now),
    )
    finals: list[float] = []
    last = None
    for resets, percent in rows:
        if last is None or last - resets >= span_s / 2:
            if len(finals) == keep:
                break
            finals.append(percent)
            last = resets
        else:
            finals[-1] = max(finals[-1], percent)
    return sum(min(f, 100.0) for f in finals) / len(finals) if finals else None
