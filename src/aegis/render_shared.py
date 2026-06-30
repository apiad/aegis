"""Medium-agnostic render helpers shared by the Rich renderer
(``aegis.render``) and the HTML renderer (``aegis.render_html``). Pure
functions and lookup tables only — no Rich, no HTML, no I/O.
"""
from __future__ import annotations

# Glyph per semantic kind (parity with ACP's tool_call kind enum; claude
# paths derive kind from the tool name in events.py).
KIND_ICON = {
    "read": "📖",
    "edit": "✏️",
    "execute": "⌬",
    "search": "🔎",
    "think": "✻",
    "fetch": "🌐",
    "move": "➡️",
    "delete": "🗑",
    "switch_mode": "🔄",
    "other": "⏺",
}

PLAN_STATUS_GLYPH = {
    "completed": "●",
    "in_progress": "◐",
    "pending": "○",
}


def pathhint(ev) -> str:
    """One-line context for a tool call: the tail of the first known
    location (with :line suffix when known), falling back to the tool's
    legacy summary string."""
    if ev.locations:
        path, line = ev.locations[0]
        tail = path.rsplit("/", 1)[-1] if path else ""
        if line is not None:
            return f"{tail}:{line}"
        return tail
    return ev.summary


def diff_window(old_text: str, new_text: str,
                max_lines: int = 6) -> tuple[list[str], list[str], int]:
    """Trim a (old_text, new_text) pair to the changed window and cap the
    visible rows. Returns ``(shown_removed, shown_added, elided)`` — removed
    rows fill the budget first, then added; ``elided`` is how many changed
    rows were dropped past ``max_lines``. Common prefix/suffix lines are
    elided — this is a change preview, not a diff viewer."""
    old_lines = old_text.splitlines() if old_text else []
    new_lines = new_text.splitlines() if new_text else []
    head = 0
    while (head < len(old_lines) and head < len(new_lines)
           and old_lines[head] == new_lines[head]):
        head += 1
    tail = 0
    while (tail < len(old_lines) - head
           and tail < len(new_lines) - head
           and old_lines[len(old_lines) - 1 - tail]
               == new_lines[len(new_lines) - 1 - tail]):
        tail += 1
    removed = old_lines[head:len(old_lines) - tail]
    added = new_lines[head:len(new_lines) - tail]

    shown_removed: list[str] = []
    shown_added: list[str] = []
    budget = max_lines
    for line in removed:
        if budget <= 0:
            break
        shown_removed.append(line)
        budget -= 1
    for line in added:
        if budget <= 0:
            break
        shown_added.append(line)
        budget -= 1
    elided = (len(removed) + len(added)) \
        - (len(shown_removed) + len(shown_added))
    return shown_removed, shown_added, elided


def result_parts(ev) -> list[str]:
    """The segments of a turn-terminator line: duration, optional cost,
    optional non-boring stop_reason. Joined with ' · ' by each renderer."""
    secs = (ev.duration_ms or 0) / 1000
    parts = [f"done in {secs:.1f}s"]
    if ev.cost_usd is not None and ev.cost_usd > 0:
        from decimal import Decimal
        from aegis.tui.metrics import _fmt_cost
        parts.append(_fmt_cost(Decimal(str(ev.cost_usd))))
    if ev.stop_reason and ev.stop_reason != "end_turn":
        parts.append(ev.stop_reason)
    return parts
