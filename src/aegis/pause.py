"""Every session paused at once, by the person (#788).

A server whose sessions together starved it comes back from a restart with
every cut turn continued (restarts.py), and the load that starved it is back
within minutes. A pause sheds that load and keeps it shed: each session's
process ends, which kills its cgroup and everything its agent left running,
and nothing new starts until the person says so.

The pause is a record in ``<state>/paused.json``, so it outlives a restart:
``aegis serve --paused`` writes it before the server boots, and Settings'
Pause all writes it on a running server. While it is there, the quota gate's
own checks hold the work: a message to a session waits in its held list, a
pending queue task stays pending, a schedule tick and a workflow node wait,
and a spawn is refused, each with ``WHY``. A turn the pause cut is marked as
the quota gate marks its own, with ``quota_paused`` set to ``WHY``.

A person's message to one session resumes that session alone; the rest stay
paused. Resume all removes the record, continues every cut turn with
``RESUMED`` ahead of what each session held, and releases what the gate held.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

RECORD = "paused.json"
WHY = "the person paused every session"
RESUMED = (
    "The person paused every session, which cut your turn. Continue where you left off."
)


def _path(state_root: Path) -> Path:
    return state_root / RECORD


def read(state_root: Path) -> dict | None:
    """The pause in force, or None."""
    try:
        doc = json.loads(_path(state_root).read_text())
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def write(state_root: Path, by: str) -> dict:
    doc = {"at": time.time(), "by": by}
    p = _path(state_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc))
    tmp.replace(p)
    return doc


def clear(state_root: Path) -> None:
    _path(state_root).unlink(missing_ok=True)
