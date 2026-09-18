"""Medium-agnostic render helpers shared by the Rich renderer
(``aegis.render``) and the HTML renderer (``aegis.render_html``). Pure
functions and lookup tables only — no Rich, no HTML, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass

from aegis.comms.descriptors import aegis_describe, aegis_glyph

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


def describe_tool(
    name: str, raw_input: dict | None, summary: str = "", locations=()
) -> str:
    """A human one-line *description* of a tool call — the collapsed line the
    transcript shows before the args are expanded. Derived from the tool's
    structured input when available, degrading to ``summary`` / location tail
    (the compact WS wire strips ``raw_input``, so callers precompute this
    server-side). Pure — no Rich, no HTML."""
    # The aegis layer answers for itself: one registry knows what matters
    # about each of its calls, and the comms ledger reads the same one.
    aegis_line = aegis_describe(name, raw_input or {})
    if aegis_line is not None:
        return aegis_line

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
        return f"{verb} {pat!r} in {where_tail}" if where_tail else f"{verb} {pat!r}"

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


def diff_counts(old_text: str, new_text: str) -> tuple[int, int]:
    """``(added, removed)`` line counts for an Edit/Write diff.

    Counted over the whole pair, not through ``diff_window``: that helper
    elides the common prefix and caps the visible rows at six, which is
    right for a preview and wrong for a count.
    """
    import difflib

    old_lines = old_text.splitlines() if old_text else []
    new_lines = new_text.splitlines() if new_text else []
    added = removed = 0
    for line in difflib.ndiff(old_lines, new_lines):
        if line.startswith("+ "):
            added += 1
        elif line.startswith("- "):
            removed += 1
    return added, removed


def result_digest(name: str, result) -> str:
    """The short verdict a tool call's single row carries after its glyph.

    One line, never more: the transcript gives a call exactly one row and
    the full output is a click away. ``result`` is the folded
    ``ToolResult``, or None while the call is still running — in flight
    there is no verdict, only a spinner.

    Pure — no Rich, no HTML.
    """
    if result is None:
        return ""
    text = result.text or ""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    # An error does not have the shape the tool's success digest reads: a
    # failed Read is a message, not a line count.
    if result.is_error:
        return _trunc(lines[0], 60) if lines else "error"
    if result.diff is not None:
        _path, old, new = result.diff
        added, removed = diff_counts(old, new)
        return f"+{added} −{removed}" if removed else f"+{added}"
    if name == "Read":
        n = len(text.splitlines())
        return f"{n} line{'s' if n != 1 else ''}"
    if name in ("Grep", "Glob"):
        n = len(lines)
        return f"{n} match{'es' if n != 1 else ''}" if n else "no matches"
    if not lines:
        return "ok"
    # Bash's verdict lives on its LAST line — "3629 passed", "Successfully
    # installed", "error: cannot find" — and first-line-wins would show the
    # progress bar instead. The cost is that `ls` and `cat` show their last
    # line rather than their first, which is noise and not a lie.
    return _trunc(lines[-1] if name == "Bash" else lines[0], 60)


def tool_label(
    name: str, raw_input: dict | None, summary: str = "", locations=()
) -> str:
    """The shortest honest name for a tool call: what a dashboard row shows.

    ``describe_tool`` is the transcript's line and pairs a Bash description
    with its command, which is exactly the detail an activity tail should
    not carry (Alex, 2026-09-17: "solo el label, no todo el bash"). Here the
    description wins alone, and a command with no description is cut.
    """
    inp = raw_input or {}
    if name == "Bash" and aegis_describe(name, inp) is None:
        desc = inp.get("description")
        if desc:
            return str(desc)
        return _trunc(inp.get("command", "") or summary, 60)
    return describe_tool(name, raw_input, summary, locations)


def tool_glyph(name: str, kind: str | None, raw_input: dict | None = None) -> str:
    """The leading glyph for a tool line: the aegis layer's own when the call
    is one of ours, else the native per-kind emoji.

    Resolved here rather than in each frontend so there is exactly one glyph
    table for the TUI, the HTML export and the web client — the web used to
    keep its own copy of ``KIND_ICON``, which is precisely the kind of
    duplicate that drifts.
    """
    return aegis_glyph(name, raw_input or {}) or KIND_ICON.get(kind or "", "⏺")


@dataclass(frozen=True)
class FileTarget:
    """The file a tool call acted on, and where in it to land.

    ``line`` is known up front (Read's ``offset``, an ACP location).
    ``anchor`` is Edit's ``old_string``: the line it starts on can only be
    found by looking at the file, which is I/O and therefore deferred to
    the moment someone actually asks to open it.
    """

    path: str
    line: int | None = None
    anchor: str | None = None


def file_target(
    name: str, raw_input: dict | None, locations=(), host: str = "local"
) -> FileTarget | None:
    """Which file (and line) a tool call points at, if any. Pure.

    ``host`` is the machine the session's harness runs on. A path from a
    remote session names a file on THAT machine; the identically-named
    local file is a different file, and opening it would be a silent
    wrong answer rather than an error. So there is no local target to
    offer — the caller shows the host-qualified path instead.
    """
    if host != "local":
        return None
    inp = raw_input or {}
    path, line, anchor = "", None, None

    if name in ("Read", "Write", "Edit"):
        path = str(inp.get("file_path") or "")
        if name == "Read":
            offset = inp.get("offset")
            if isinstance(offset, int) and offset > 0:
                line = offset
        elif name == "Edit":
            old = inp.get("old_string")
            if isinstance(old, str) and old.strip():
                anchor = old

    if not path and locations:
        loc_path, loc_line = locations[0]
        path = str(loc_path or "")
        line = loc_line

    return FileTarget(path, line, anchor) if path else None


def anchor_line(text: str, anchor: str) -> int | None:
    """The 1-based line ``anchor`` starts on in ``text``, or None.

    An edit's ``old_string`` is gone from the file by the time you click
    the block — the whole point of an edit — and the file may have moved on
    further still. So: try the anchor verbatim, then its opening line
    alone, then give up. A wrong line is worse than the top of the file.
    """
    if not text or not anchor:
        return None
    for needle in (anchor, anchor.splitlines()[0] if anchor else ""):
        if not needle:
            continue
        idx = text.find(needle)
        if idx >= 0:
            return text.count("\n", 0, idx) + 1
    return None


def format_tool_args(
    name: str, raw_input: dict | None, summary: str = "", cap: int | None = 500
) -> str:
    """The full-args view of a tool call, shown in its detail window.
    Bash shows its command verbatim (with the description as a leading
    comment); other tools show ``key: value`` lines with long values capped
    at ``cap`` characters — ``cap=None`` for the window itself, which is the
    one place that wants every character.
    Pure — no Rich, no HTML."""
    import json

    inp = raw_input or {}
    if not inp:
        return summary or ""
    if name == "Bash" and inp.get("command"):
        out = []
        if inp.get("description"):
            out.append(f"# {inp['description']}")
        out.append(str(inp["command"]))
        return "\n".join(out)
    lines = []
    for k, v in inp.items():
        val = (
            v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)
        )
        if cap is not None and len(val) > cap:
            val = val[:cap] + "…"
        lines.append(f"{k}: {val}")
    return "\n".join(lines)


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


def diff_window(
    old_text: str, new_text: str, max_lines: int = 6
) -> tuple[list[str], list[str], int]:
    """Trim a (old_text, new_text) pair to the changed window and cap the
    visible rows. Returns ``(shown_removed, shown_added, elided)`` — removed
    rows fill the budget first, then added; ``elided`` is how many changed
    rows were dropped past ``max_lines``. Common prefix/suffix lines are
    elided — this is a change preview, not a diff viewer."""
    old_lines = old_text.splitlines() if old_text else []
    new_lines = new_text.splitlines() if new_text else []
    head = 0
    while (
        head < len(old_lines)
        and head < len(new_lines)
        and old_lines[head] == new_lines[head]
    ):
        head += 1
    tail = 0
    while (
        tail < len(old_lines) - head
        and tail < len(new_lines) - head
        and old_lines[len(old_lines) - 1 - tail] == new_lines[len(new_lines) - 1 - tail]
    ):
        tail += 1
    removed = old_lines[head : len(old_lines) - tail]
    added = new_lines[head : len(new_lines) - tail]

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
    elided = (len(removed) + len(added)) - (len(shown_removed) + len(shown_added))
    return shown_removed, shown_added, elided


def format_age(seconds: float) -> str:
    """How long ago something happened, at a glance: 'just now', '12s ago',
    '4m ago', '1h 5m ago', '1d 2h ago'."""
    s = int(max(0, seconds))
    if s < 10:
        return "just now"
    if s < 60:
        return f"{s}s ago"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m ago"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h}h {m}m ago" if m else f"{h}h ago"
    d, h = divmod(h, 24)
    return f"{d}d {h}h ago" if h else f"{d}d ago"


def result_parts(ev, *, age_s: float | None = None) -> list[str]:
    """The segments of a turn-terminator line: duration, optional cost,
    optional non-boring stop_reason, and — when the caller can say how long
    ago the turn ended — that age. Joined with ' · ' by each renderer."""
    secs = (ev.duration_ms or 0) / 1000
    parts = [f"done in {secs:.1f}s"]
    if ev.cost_usd is not None and ev.cost_usd > 0:
        from decimal import Decimal
        from aegis.tui.metrics import _fmt_cost

        parts.append(_fmt_cost(Decimal(str(ev.cost_usd))))
    if ev.stop_reason and ev.stop_reason != "end_turn":
        parts.append(ev.stop_reason)
    if age_s is not None:
        parts.append(format_age(age_s))
    return parts
