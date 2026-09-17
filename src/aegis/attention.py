"""Whether a finished turn needs the operator.

Every turn recap carries one category. The model proposes it, because only
the model can tell a question from a report; hard signals decide, because
the model cannot see a failed Result, cannot know a session is a queue
worker whose answer goes elsewhere, and cannot see a monitor it armed.

Seen-ness is per view (``is_pending``), like the unread ``*``: the brain
holds the category and a sequence number, and each pane remembers the
sequence it last showed. ``waiting`` is shown while it holds, seen or not.
"""

from __future__ import annotations

CATEGORIES: tuple[str, ...] = ("needs_input", "error", "review", "waiting", "done")

LABELS: dict[str, str] = {
    "needs_input": "needs you",
    "error": "error",
    "review": "review",
    "waiting": "waiting",
    "done": "done",
}


def resolve(model: str | None, *, errored: bool, ephemeral: bool, waiting: bool) -> str:
    """The category a turn ends with. See the spec's precedence list."""
    if errored:
        return "error"
    cat = model if model in CATEGORIES else "done"
    if ephemeral and cat == "needs_input":
        cat = "done"
    if waiting and cat not in ("error", "needs_input"):
        cat = "waiting"
    return cat


def is_pending(category: str, seq: int, acked: int) -> bool:
    if category not in CATEGORIES:
        return False
    return category == "waiting" or seq > acked


def style_for(category: str, colors) -> str:
    return {
        "needs_input": colors.accent,
        "error": colors.error,
        "review": colors.ink,
        "waiting": colors.muted,
        "done": colors.ready,
    }.get(category, colors.muted)


def mark(category: str, colors, *, blink_off: bool = False) -> str:
    """Rich markup for the category's mark. The two urgent ones sit on a
    filled block; ``blink_off`` blanks the glyph but keeps the cells, so a
    blinking tab never shifts the tabs after it."""
    if category == "needs_input":
        glyph = " " if blink_off else "?"
        return f"[bold {colors.panel} on {colors.accent}] {glyph} [/]"
    if category == "error":
        return f"[bold {colors.panel} on {colors.error}] ✗ [/]"
    if category == "review":
        return f"[bold {colors.ink}]◆[/]"
    if category == "waiting":
        return f"[{colors.muted}]⧗[/]"
    if category == "done":
        return f"[{colors.ready}]✓[/]"
    return ""
