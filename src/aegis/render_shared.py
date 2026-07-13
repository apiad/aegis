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


def _trunc(s: str, n: int) -> str:
    """Collapse whitespace and cap length with an ellipsis."""
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _loc_tail(locations) -> str:
    if locations:
        path, line = locations[0]
        tail = path.rsplit("/", 1)[-1] if path else ""
        return f"{tail}:{line}" if line is not None else tail
    return ""


def describe_tool(name: str, raw_input: dict | None,
                  summary: str = "", locations=()) -> str:
    """A human one-line *description* of a tool call — the collapsed line the
    transcript shows before the args are expanded. Derived from the tool's
    structured input when available, degrading to ``summary`` / location tail
    (the compact WS wire strips ``raw_input``, so callers precompute this
    server-side). Pure — no Rich, no HTML."""
    inp = raw_input or {}

    if name == "Bash":
        desc = inp.get("description")
        cmd = _trunc(inp.get("command", ""), 60)
        if desc and cmd:
            return f"{desc}  ·  {cmd}"
        return str(desc) if desc else (cmd or summary)

    if name in ("Read", "Write"):
        p = inp.get("file_path", "")
        tail = p.rsplit("/", 1)[-1] if p else _loc_tail(locations)
        verb = "read" if name == "Read" else "write"
        return f"{verb} {tail}" if tail else (summary or verb)

    if name == "Edit":
        p = inp.get("file_path", "")
        tail = p.rsplit("/", 1)[-1] if p else _loc_tail(locations)
        old = _trunc(inp.get("old_string", ""), 30)
        if tail and old:
            return f"edit {tail}: {old}"
        return f"edit {tail}" if tail else (summary or "edit")

    if name in ("Grep", "Glob"):
        pat = inp.get("pattern", "")
        where = inp.get("path") or inp.get("glob") or ""
        where_tail = where.rsplit("/", 1)[-1] if where else ""
        verb = "grep" if name == "Grep" else "glob"
        if not pat:
            return summary or verb
        return f"{verb} {pat!r} in {where_tail}" if where_tail \
            else f"{verb} {pat!r}"

    if name in ("WebFetch", "WebSearch"):
        return _trunc(inp.get("url") or inp.get("query", "") or summary, 70)

    if name in ("Task", "Agent"):
        d = inp.get("description") or inp.get("subagent_type") or summary
        return f"subagent: {d}" if d else "subagent"

    if name == "TodoWrite":
        todos = inp.get("todos") or []
        return f"update plan ({len(todos)} items)"

    # Unknown tool: first stringy arg, else summary, else location tail, else
    # the bare tool name so the line is never empty.
    for v in inp.values():
        if isinstance(v, str) and v.strip():
            return _trunc(v, 60)
    return summary or _loc_tail(locations) or name


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
