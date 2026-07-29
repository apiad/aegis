"""Damage-tolerant JSONL history reader for web subscribe + resume.

The session log stores no ``seq`` of its own, so one is synthesized here
as the 1-based *record* index. It has to number records rather than
lines because ``SubscriptionRegistry`` seeds ``hs.seq = len(history)``
and then increments per live event: numbering lines would leave
``seq > len(history)`` after any skipped line, and the ``seq > current``
dedup would then silently drop every subsequent live event.

Damaged records are skipped wherever they sit. Interior damage is the
normal shape once a session resumes into a log it already crashed in
(see ``aegis.state.session_log``), so treating it as fatal only moved
the outage from one transcript to the whole web session.
"""
from __future__ import annotations

from pathlib import Path

from aegis.events import Event
from aegis.state.event_codec import decode_event
from aegis.state.session_log import scan_log, session_log_path


def read_history(state_dir: Path, log_id: str) -> list[tuple[int, Event]]:
    """Return ``(seq, event)`` pairs for ``log_id``'s session log.

    ``seq`` is the 1-based index among readable records. A missing file
    yields ``[]``.
    """
    out: list[tuple[int, Event]] = []
    for rec in scan_log(session_log_path(Path(state_dir), log_id)).records:
        try:
            ev = decode_event(rec["event"])
        except Exception:  # noqa: BLE001 — one unreadable record, not a log
            continue
        out.append((len(out) + 1, ev))
    return out
