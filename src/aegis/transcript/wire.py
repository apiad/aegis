"""What a transcript entry looks like on the wire.

The fold keeps every entry whole. A browser gets each one without what a closed
``<details>`` shows: a tool's arguments, output tail and diff, a system note's
tail, a typed command's template, and thinking text. A row fetches them with
``transcript.detail`` when it opens. Over 80 real transcripts those fields were
75% of a snapshot's bytes, and nobody reads them until they open the row.

``more`` tells the browser a row has something to fetch. Python decides what is
lazy; the browser only asks for it (DESIGN.md, "Python decides, the browser
draws").
"""

from __future__ import annotations

# Per entry kind, the detail fields a closed row hides. A recap has none: its
# detail is what its row shows, open or folded.
LAZY: dict[str, tuple[str, ...]] = {
    "tool": ("args", "tail", "diff", "peek"),
    "system": ("tail",),
    "user": ("tail",),
    "artifact": ("events",),
}


def wire(e: dict) -> dict:
    """``e`` as a browser receives it; ``e`` itself when nothing is lazy."""
    if e["kind"] == "thinking":
        if not e.get("md"):
            return e
        return {**e, "md": None, "detail": {**e["detail"], "more": True}}
    lazy = [k for k in LAZY.get(e["kind"], ()) if e["detail"].get(k)]
    if not lazy:
        return e
    detail = {k: v for k, v in e["detail"].items() if k not in lazy}
    return {**e, "detail": {**detail, "more": True}}


def wire_ops(ops: list[dict]) -> list[dict]:
    """Patch ops as a browser receives them."""
    return [{"upsert": wire(op["upsert"])} if "upsert" in op else op for op in ops]


def withheld_text(e: dict) -> str:
    """The text an open row shows that ``wire`` kept from the browser: a tool's
    arguments, output tail and diff, a note's or command's tail, and thinking.
    The find bar searches it here (``transcript.search``), since the browser
    holds the rest. A peek's file card and a page's events are not text a row
    shows, so they are not searched."""
    if e["kind"] == "thinking":
        return e.get("md") or ""
    d, lazy = e["detail"], LAZY.get(e["kind"], ())
    parts = [d[k] for k in ("args", "tail") if k in lazy and d.get(k)]
    if diff := d.get("diff"):
        parts += [diff["path"], *diff["removed"], *diff["added"]]
    return "\n".join(parts)
