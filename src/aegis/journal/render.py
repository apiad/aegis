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


def text(
    hits: list[Hit], cut: bool, root: str, handles: dict[str, str] | None = None
) -> str:
    """``handles``: each open session's current handle by log id, named beside
    an entry made under an older one, since peer_read takes only the current."""
    out: list[str] = []
    day = None
    for h in hits:
        if _day(h.ts) != day:
            day = _day(h.ts)
            out.append(day)
        tag = f"{h.tag}: " if h.tag else ""
        now = (handles or {}).get(h.log_id)
        who = f"{h.handle} (now {now})" if now and now != h.handle else h.handle
        out.append(f"  {_time(h.ts)}  {who}  {h.kind}  {tag}{h.text}")
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


def rows(
    hits: list[Hit], root: str, open_ids: set[str], marks: list[list[int]] | None = None
) -> list[dict]:
    """``marks``: per hit, the indices in its text the view's box matched."""
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
            "marks": marks[i] if marks else [],
        }
        for i, h in enumerate(hits)
    ]
