"""aegis's own transcript store, as the usage readers see it.

One session is a meta (``sessions/<log_id>.json``) and a store
(``transcripts/<log_id>.jsonl``) whose ``src: "claude"`` records wrap Claude's
stream-json lines verbatim. Those lines carry what the readers need: an
assistant line has the message id, model and usage, the 5-minute/1-hour cache
split included, and a ``result`` line has the turn's usage and Claude's running
cost. The time is the record's ``ts``; the working directory is the spawn
record's ``cwd``.

Sessions imported from the legacy tree (``aegis import-legacy``) live here too,
so nothing here reads the legacy directory.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class StoredSession:
    log_id: str
    handle: str
    harness: str
    path: Path


def iso(ts: float | None) -> str | None:
    """A record's epoch ``ts`` as the UTC ISO string the readers compare."""
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def sessions(state_dir: Path) -> Iterator[StoredSession]:
    """Every stored transcript, with its meta's handle when it has one."""
    tdir = state_dir / "transcripts"
    if not tdir.is_dir():
        return
    for path in sorted(tdir.glob("*.jsonl")):
        meta: dict = {}
        try:
            meta = json.loads(
                (state_dir / "sessions" / f"{path.stem}.json").read_text()
            )
        except (OSError, ValueError):
            pass
        yield StoredSession(
            log_id=path.stem,
            handle=str(meta.get("handle") or path.stem),
            harness=str(meta.get("harness") or "claude-code"),
            path=path,
        )


@dataclass
class Line:
    ts: str | None
    src: str
    # The parsed harness line for src "claude" and "opencode", else the aegis
    # record itself.
    obj: dict
    # The text as stored, for the readers that search it.
    raw: str


def lines(path: Path) -> Iterator[Line]:
    """The intact records of one store, the harness's lines parsed. A damaged
    line is skipped, never fatal."""
    try:
        f = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return
    with f:
        for raw in f:
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            src = str(rec.get("src") or "")
            obj = rec
            if src in ("claude", "opencode"):
                try:
                    obj = json.loads(rec.get("line") or "")
                except (ValueError, TypeError):
                    continue
                if not isinstance(obj, dict):
                    continue
            yield Line(ts=iso(rec.get("ts")), src=src, obj=obj, raw=raw)


def split_cache(usage: dict) -> tuple[int, int, bool]:
    """(5-minute writes, 1-hour writes, whether Claude reported the split). A
    line without the split, which imported legacy sessions are, reports all
    writes as 5-minute ones."""
    creation = usage.get("cache_creation")
    if isinstance(creation, dict):
        cc5 = int(creation.get("ephemeral_5m_input_tokens") or 0)
        cc1 = int(creation.get("ephemeral_1h_input_tokens") or 0)
        if cc5 + cc1:
            return cc5, cc1, True
    return int(usage.get("cache_creation_input_tokens") or 0), 0, False
