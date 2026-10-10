"""The journal as text, for agents and the CLI, and as rows, for the browser.
Python decides every field a row shows; the client only draws them."""

from __future__ import annotations

import os
import time

from . import when
from .db import Hit

# A glyph is the name of a symbol in the client's sprite (js/glyphs.js), as
# the transcript's gutter sends (transcript/describe.py): a character would
# render in whatever font the system falls back to.
GLYPH = {
    "turn": "prompt",
    "commit": "dot",
    "pr": "arrow-up",
    "plan": "read",
    "note": "pencil",
    "session": "window",
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


def _hash(h: Hit, marks: list[int]) -> tuple[str, list[int], str, list[int]]:
    """A commit's short hash apart from the rest of its text, and the marks
    split between them, each indexed into its own part."""
    sp = h.text.find(" ") if h.kind == "commit" else -1
    if sp <= 0:
        return "", [], h.text, marks
    head = [m for m in marks if m < sp]
    return h.text[:sp], head, h.text[sp + 1 :], [m - sp - 1 for m in marks if m > sp]


def rows(
    hits: list[Hit], root: str, open_ids: set[str], marks: list[list[int]] | None = None
) -> list[dict]:
    """``marks``: per hit, the indices in its text the view's box matched. A
    commit's hash comes apart from its text, each with its own marks. ``day``
    is the label the view's day pill shows, which names the server's today."""
    today = when.today()
    out = []
    for i, h in enumerate(hits):
        commit, commit_marks, text, text_marks = _hash(h, marks[i] if marks else [])
        day = _day(h.ts)
        out.append(
            {
                "id": h.id,
                "day": f"{day} · today" if day.startswith(today) else day,
                "time": _time(h.ts),
                "glyph": GLYPH.get(h.kind, "pip"),
                "kind": h.kind,
                "tag": h.tag,
                "handle": h.handle,
                "log_id": h.log_id,
                "open": h.log_id in open_ids,
                "hash": commit,
                "hash_marks": commit_marks,
                "text": text,
                "paths": [shown(p, root) for p in h.paths[:SHOWN]],
                "more": max(0, len(h.paths) - SHOWN),
                "source": h.source,
                "marks": text_marks,
            }
        )
    return out
