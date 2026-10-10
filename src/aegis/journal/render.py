"""The journal as text, for agents and the CLI, and as rows, for the browser.
Python decides every field a row shows; the client only draws them."""

from __future__ import annotations

import os
import time

from .db import Hit

GLYPH = {
    "turn": "◆",
    "commit": "●",
    "pr": "⇡",
    "plan": "✓",
    "note": "✎",
    "session": "○",
}
SHOWN = 3


def shown(full: str, root: str) -> str:
    rel = os.path.relpath(full, root)
    return full if rel.startswith("..") else rel


def _day(ts: float) -> str:
    return time.strftime("%Y-%m-%d %a", time.localtime(ts))


def _time(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(ts))


def text(hits: list[Hit], cut: bool, root: str) -> str:
    out: list[str] = []
    day = None
    for h in hits:
        if _day(h.ts) != day:
            day = _day(h.ts)
            out.append(day)
        tag = f"{h.tag}: " if h.tag else ""
        out.append(f"  {_time(h.ts)}  {h.handle}  {h.kind}  {tag}{h.text}")
        if h.paths:
            more = f"  +{len(h.paths) - SHOWN} more" if len(h.paths) > SHOWN else ""
            out.append(
                "         " + "  ".join(shown(p, root) for p in h.paths[:SHOWN]) + more
            )
        elif h.files_unknown:
            out.append(
                "         files unknown: the commit was not in the repo aegis guessed"
            )
    out.append(
        f"{len(hits)} entries"
        + ("; more match: narrow the filters or raise limit" if cut else "")
    )
    return "\n".join(out)


def rows(hits: list[Hit], root: str, open_ids: set[str]) -> list[dict]:
    return [
        {
            "id": h.id,
            "day": _day(h.ts),
            "time": _time(h.ts),
            "glyph": GLYPH.get(h.kind, "·"),
            "kind": h.kind,
            "tag": h.tag,
            "handle": h.handle,
            "log_id": h.log_id,
            "open": h.log_id in open_ids,
            "text": h.text,
            "paths": [shown(p, root) for p in h.paths[:SHOWN]],
            "more": max(0, len(h.paths) - SHOWN),
            "source": h.source,
        }
        for h in hits
    ]
