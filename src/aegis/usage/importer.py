"""The one-time import of Claude Code's own session files (spec 13).

``~/.claude/projects`` holds every session Claude Code ran, with or without
aegis, and purges them after thirty days. Each file becomes one session,
``cc-<file stem>``, fed to the same ``UsageDeriver`` as aegis's stores, with
every turn of mode ``outside`` and its repo from the file's working directory.

These files have no result lines, so a turn ends at the next prompt (a user
line that is not a tool's result) and at the end of the file. A message id
counts once: one an aegis store holds is skipped, and so is one another
imported file already holds (a resumed session's file repeats its history).
A file whose session aegis ran itself is skipped whole, since its store counts
it live.

A file is recorded in ``usage_imports`` with its size and mtime once its rows
are in, so a second run reads nothing, a run stopped half way resumes at the
next file, and a file that grew is derived again from its first line after
its old rows are dropped (its last turn may have gone on).
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..journal import paths
from ..journal.service import _scratch
from . import tables
from .owners import owner_of
from .store import sessions as stored_sessions
from .turns import Derived, SessionInfo, UsageDeriver

ROW_TABLES = ("usage_turns", "usage_parts", "usage_waits", "usage_ctx", "usage_whatif")


@dataclass
class ImportReport:
    files: int = 0  # files found
    sessions: int = 0  # files that were derived and added rows
    turns: int = 0
    already: int = 0  # unchanged since the last run
    live: int = 0  # sessions aegis ran itself, counted from its stores
    unreadable: list[str] = field(default_factory=list)


def aegis_known(state_root: Path) -> set[str]:
    """Every Claude message id and Claude session id in aegis's stores: what
    aegis already counts."""
    known: set[str] = set()
    for s in stored_sessions(state_root):
        try:
            with s.path.open(encoding="utf-8", errors="replace") as f:
                for raw in f:
                    if '"claude"' not in raw:
                        continue
                    _note(known, raw)
        except OSError:
            continue
    return known


def _note(known: set[str], raw: str) -> None:
    try:
        rec = json.loads(raw)
        obj = json.loads(rec.get("line") or "")
    except (ValueError, TypeError, AttributeError):
        return
    if not isinstance(obj, dict):
        return
    if obj.get("subtype") == "init" and obj.get("session_id"):
        known.add(str(obj["session_id"]))
    message = obj.get("message")
    if obj.get("type") == "assistant" and isinstance(message, dict):
        if message.get("id"):
            known.add(str(message["id"]))


def _files(roots: Iterable[Path]) -> list[Path]:
    found: list[Path] = []
    for root in roots:
        if root.is_dir():
            found += root.rglob("*.jsonl")
    return sorted(found)


def _epoch(ts: object) -> float | None:
    if not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _prompt(obj: dict) -> str | None:
    """A user line's prompt text, or None for a tool's result."""
    content = (obj.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return None
        return " ".join(
            str(b.get("text") or "") for b in content if isinstance(b, dict)
        )
    return None


def _records(lines: list[str], skip: set[str]) -> tuple[list[dict], set[str]]:
    """A project file's lines as store records, and the message ids kept."""
    recs: list[dict] = []
    ids: set[str] = set()
    ts = 0.0
    spawned = False
    for i, raw in enumerate(lines):
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(obj, dict) or obj.get("type") not in ("user", "assistant"):
            continue
        ts = _epoch(obj.get("timestamp")) or ts
        if not spawned:
            recs.append({"i": i, "ts": ts, "src": "aegis", "kind": "spawn"})
            recs[-1]["cwd"] = str(obj.get("cwd") or "")
            spawned = True
        if obj.get("type") == "assistant":
            mid = str((obj.get("message") or {}).get("id") or "")
            if mid in skip:
                continue
            if mid:
                ids.add(mid)
        elif (text := _prompt(obj)) is not None:
            recs.append({"i": i, "ts": ts, "src": "aegis", "kind": "exit"})
            recs.append(
                {"i": i, "ts": ts, "src": "aegis", "kind": "send", "text": text}
            )
            continue
        recs.append({"i": i, "ts": ts, "src": "claude", "line": raw})
    if spawned:
        recs.append({"i": len(lines), "ts": ts, "src": "aegis", "kind": "close"})
    return recs, ids


def _repo_of(user: str | None) -> Callable[[str], str | None]:
    cache: dict[str, str | None] = {}

    def repo_of(d: str) -> str | None:
        if d not in cache:
            cache[d] = None if _scratch(d) else paths.place(d, user)[0]
        return cache[d]

    return repo_of


def _drop(con, log_id: str, path: str) -> None:
    con.execute("BEGIN")
    try:
        for t in ROW_TABLES:
            con.execute(f"DELETE FROM {t} WHERE log_id = ?", (log_id,))
        con.execute("DELETE FROM usage_import_ids WHERE path = ?", (path,))
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise


def _done(con, path: str, size: int, mtime: float, ids: set[str]) -> None:
    con.execute("BEGIN")
    try:
        con.executemany(
            "INSERT INTO usage_import_ids VALUES(?, ?) ON CONFLICT DO NOTHING",
            [(m, path) for m in ids],
        )
        con.execute(
            "INSERT INTO usage_imports VALUES(?, ?, ?) ON CONFLICT(path)"
            " DO UPDATE SET size = excluded.size, mtime = excluded.mtime",
            (path, size, mtime),
        )
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise


def import_claude_projects(
    con,
    roots: list[Path],
    known_ids: set[str],
    person: str,
    progress: Callable[[int, int], None] = lambda done, total: None,
    *,
    user: str | None = None,
) -> ImportReport:
    """Import every ``*.jsonl`` under ``roots`` as sessions of ``person``;
    ``user`` is the Linux user their repos are read as."""
    report = ImportReport()
    files = _files(roots)
    report.files = len(files)
    repo_of = _repo_of(user)
    owned: set[str] = set()
    for k, f in enumerate(files):
        path = str(f)
        try:
            st = f.stat()
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            report.unreadable.append(path)
            progress(k + 1, len(files))
            continue
        size, mtime = st.st_size, st.st_mtime
        seen = con.execute(
            "SELECT size, mtime FROM usage_imports WHERE path = ?", (path,)
        ).fetchone()
        if seen is not None and tuple(seen) == (size, mtime):
            report.already += 1
        elif f.stem in known_ids:
            report.live += 1
            _done(con, path, size, mtime, set())
        else:
            log_id = f"cc-{f.stem}"
            _drop(con, log_id, path)
            others = {
                m
                for (m,) in con.execute(
                    "SELECT mid FROM usage_import_ids WHERE path != ?", (path,)
                )
            }
            recs, ids = _records(lines, known_ids | others)
            u = UsageDeriver(SessionInfo(person=person, kind="outside"), repo_of)
            d = Derived()
            for r in recs:
                d.extend(u.feed(r))
            if d:
                tables.insert(con, log_id, d)
                report.sessions += 1
                report.turns += len(d.turns)
                for repo in {p.repo for p in d.parts if p.repo} - owned:
                    owned.add(repo)
                    if os.path.isdir(repo):
                        owner_of(con, repo, repo, user, {})
            _done(con, path, size, mtime, ids)
        progress(k + 1, len(files))
    return report
