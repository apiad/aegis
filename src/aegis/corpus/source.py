"""Read a session log, merged with its backfill sidecar, as (event, ts) pairs.

Sidecars live in `state/backfill/`, never in `state/sessions/` — three
separate call sites glob `sessions/*.jsonl` and would read a sidecar back as
a session log. The merge happens here, at read time, so an existing log is
never rewritten.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from aegis.corpus.extract import Exchange, extract_exchanges
from aegis.state.session_log import parse_log_id

_FROM_RE = re.compile(r"^>\s*from\s+([a-z]+)(?::([^\s·]+))?", re.I)
_KNOWN = {"queue", "agent", "monitor", "canvas", "term",
          "workflow", "loop", "reminder"}
_HARNESS_PREFIXES = (
    "<task-notification>", "<system-reminder>", "<command-name>",
    "<local-command-stdout>", "<user-prompt-submit-hook>",
    "Base directory for this skill:",
)


def derive_source(text: str) -> tuple[str, str | None]:
    """Provenance from the substrate's own header. Import path only —
    the live path uses the pending-send table (VS2), because an operator
    can legitimately type a line starting with '> from'."""
    s = (text or "").lstrip()
    m = _FROM_RE.match(s)
    if m:
        kind = m.group(1).lower()
        return (kind if kind in _KNOWN else "substrate"), m.group(2)
    if s.startswith(_HARNESS_PREFIXES):
        return "harness", None
    return "operator", None


def _iter_records(path: Path):
    if not path.exists():
        return
    with path.open(errors="replace") as fh:
        for line in fh:
            try:
                yield json.loads(line)
            except Exception:
                continue


def read_log(log_path: Path, backfill_dir: Path | None):
    """-> ([(event_dict, aegis_ts), ...] in timestamp order, meta dict)"""
    records = list(_iter_records(log_path))
    if backfill_dir is not None:
        records += list(_iter_records(Path(backfill_dir) / log_path.name))

    records.sort(key=lambda r: r.get("aegis_ts") or "")

    # The filename is `<birthtime>-<handle>`, so it answers "whose session
    # is this" even when the log carries no SessionMeta record — which is
    # the majority of the corpus. A real record overwrites this below: a
    # rename appends one, so it is the more current name.
    _birth, birth_handle = parse_log_id(Path(log_path).stem)
    meta: dict = {"handle": birth_handle or None, "cwd": None,
                  "profile": None, "host": None}
    events: list[tuple[dict, str]] = []
    for r in records:
        ev = r.get("event") or {}
        ts = r.get("aegis_ts") or ""
        if ev.get("t") == "SessionMeta":
            meta = {"handle": ev.get("handle"), "cwd": ev.get("cwd"),
                    "profile": ev.get("profile"), "host": ev.get("host")}
        if ev.get("t") == "UserMessage" and not ev.get("source"):
            src, sender = derive_source(ev.get("text", ""))
            ev = {**ev, "source": src, "sender": sender}
        events.append((ev, ts))
    return events, meta


def exchanges_for_log(log_path: Path, backfill_dir: Path | None) -> list[Exchange]:
    events, meta = read_log(Path(log_path), backfill_dir)
    return extract_exchanges(events, meta)
