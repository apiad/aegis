"""Torn-tolerant JSONL history reader for web subscribe + resume.

Per the S1 persistence audit (see the web-client design spec): the session
log is a flat ``<state_dir>/sessions/<handle>.jsonl``, lines are
``{"v":1,"aegis_ts":<iso>,"event":<encoded>}`` with no stored ``seq``, and
``append_event`` does not fsync. So we synthesize ``seq`` as the 1-based line
index and tolerate a torn trailing line (a crash mid-append), while treating
malformed interior lines as genuine corruption.
"""
from __future__ import annotations

import json
from pathlib import Path

from aegis.events import Event
from aegis.state.event_codec import decode_event
from aegis.state.session_log import session_log_path


def read_history(state_dir: Path, handle: str) -> list[tuple[int, Event]]:
    """Return ``(seq, event)`` pairs for ``handle``'s session log.

    ``seq`` is the 1-based line index. A missing file yields ``[]``. An
    unparseable *trailing* line is dropped (torn write); an unparseable
    *interior* line raises ``ValueError``.
    """
    p = session_log_path(Path(state_dir), handle)
    if not p.exists():
        return []
    lines = p.read_text(encoding="utf-8").splitlines()
    out: list[tuple[int, Event]] = []
    last = len(lines) - 1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            rec = json.loads(stripped)
            ev = decode_event(rec["event"])
        except Exception as exc:
            if i == last:
                break  # torn trailing write — tolerate
            raise ValueError(
                f"corrupt interior line {i + 1} in {p}") from exc
        out.append((i + 1, ev))
    return out
