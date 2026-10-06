"""What a tool call says about itself: glyph, label, result, args, diff.

Copied from the old tree's ``render_shared.py`` and trimmed to what slice 1
draws. Pure functions; the browser receives their output as entry fields and
never computes any of it (DESIGN.md, "Python decides, the browser draws").
"""

from __future__ import annotations

import difflib
import json

KIND_BY_NAME = {
    "Read": "read",
    "Bash": "execute",
    "BashOutput": "execute",
    "KillShell": "execute",
    "Edit": "edit",
    "MultiEdit": "edit",
    "Write": "edit",
    "NotebookEdit": "edit",
    "Glob": "search",
    "Grep": "search",
    "WebFetch": "fetch",
    "WebSearch": "fetch",
    "Task": "think",
    "Agent": "think",
}

KIND_GLYPH = {
    "read": "📖",
    "edit": "✎",
    "execute": "⌬",
    "search": "🔎",
    "think": "✻",
    "fetch": "🌐",
    "other": "⏺",
}

USER_GLYPH = "❯"
PROSE_GLYPH = "⏺"
THINKING_GLYPH = "✻"
SYSTEM_GLYPH = "·"
ERROR_GLYPH = "✗"

# How much of a result the one-line verdict may carry.
DIGEST_MAX = 200


def _trunc(s: str, n: int) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _tail(path: str) -> str:
    return path.rsplit("/", 1)[-1] if path else ""


def tool_glyph(name: str) -> str:
    return KIND_GLYPH[KIND_BY_NAME.get(name, "other")]


def tool_label(name: str, inp: dict) -> str:
    """The shortest honest name for a call: which call it was, without its
    arguments, except a search's pattern, which is which search it was."""
    if name == "Bash":
        if inp.get("description"):
            return str(inp["description"])
        return _trunc(inp.get("command", ""), 60)
    if name in ("Read", "Write", "Edit", "MultiEdit", "NotebookEdit"):
        tail = _tail(str(inp.get("file_path") or inp.get("notebook_path") or ""))
        verb = {"Read": "read", "Write": "write"}.get(name, "edit")
        return f"{verb} {tail}" if tail else verb
    if name in ("Grep", "Glob"):
        pat = inp.get("pattern", "")
        verb = "grep" if name == "Grep" else "glob"
        return f"{verb} {pat!r}" if pat else verb
    if name in ("WebFetch", "WebSearch"):
        return _trunc(inp.get("url") or inp.get("query", ""), 70)
    if name in ("Task", "Agent"):
        d = inp.get("description") or inp.get("subagent_type") or ""
        return f"subagent: {d}" if d else "subagent"
    if name == "TodoWrite":
        return f"update plan ({len(inp.get('todos') or [])} items)"
    for v in inp.values():
        if isinstance(v, str) and v.strip():
            return _trunc(v, 60)
    return name


def diff_counts(old_text: str, new_text: str) -> tuple[int, int]:
    added = removed = 0
    for line in difflib.ndiff(old_text.splitlines(), new_text.splitlines()):
        if line.startswith("+ "):
            added += 1
        elif line.startswith("- "):
            removed += 1
    return added, removed


def diff_window(
    old_text: str, new_text: str, max_lines: int = 6
) -> tuple[list[str], list[str], int]:
    """The changed window of an edit, capped at ``max_lines`` rows: removed
    rows first, then added. Returns ``(removed, added, elided)``."""
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
    shown_r = removed[:max_lines]
    shown_a = added[: max(0, max_lines - len(shown_r))]
    elided = len(removed) + len(added) - len(shown_r) - len(shown_a)
    return shown_r, shown_a, elided


def edit_pair(name: str, inp: dict) -> tuple[str, str, str] | None:
    """``(path, old, new)`` for a call that changes a file, else None."""
    path = inp.get("file_path")
    if not isinstance(path, str):
        return None
    if name == "Edit":
        old, new = inp.get("old_string", ""), inp.get("new_string", "")
        if isinstance(old, str) and isinstance(new, str):
            return path, old, new
    if name == "Write" and isinstance(inp.get("content"), str):
        return path, "", inp["content"]
    return None


def result_digest(
    name: str, text: str, is_error: bool, pair: tuple[str, str, str] | None
) -> str:
    """The one-line verdict a tool row carries after it finishes."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    # Claude Code swaps an oversized result for a wrapper around a 2 KB
    # preview; its size is the only honest verdict.
    if lines and lines[0] == "<persisted-output>" and len(lines) > 1:
        return _trunc(lines[1].split(". Full output")[0], DIGEST_MAX)
    # A failed Bash is headed "Exit code N" and still ends on its verdict.
    if (
        is_error
        and name == "Bash"
        and len(lines) > 1
        and lines[0].startswith("Exit code ")
    ):
        return _trunc(f"{lines[0]} · {lines[-1]}", DIGEST_MAX)
    if is_error:
        return _trunc(lines[0], DIGEST_MAX) if lines else "error"
    if pair is not None:
        added, removed = diff_counts(pair[1], pair[2])
        return f"+{added} −{removed}" if removed else f"+{added}"
    if name == "Read":
        n = len(text.splitlines())
        return f"{n} line{'s' if n != 1 else ''}"
    if name in ("Grep", "Glob"):
        n = len(lines)
        return f"{n} match{'es' if n != 1 else ''}" if n else "no matches"
    if not lines:
        return "ok"
    # Bash's verdict is on its last line ("3629 passed").
    return _trunc(lines[-1] if name == "Bash" else lines[0], DIGEST_MAX)


def format_tool_args(name: str, inp: dict, cap: int = 500) -> str:
    """The full arguments of a call, for its expanded view."""
    if not inp:
        return ""
    if name == "Bash" and inp.get("command"):
        out = [f"# {inp['description']}"] if inp.get("description") else []
        out.append(str(inp["command"]))
        return "\n".join(out)
    lines = []
    for k, v in inp.items():
        val = (
            v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)
        )
        if len(val) > cap:
            val = val[:cap] + "…"
        lines.append(f"{k}: {val}")
    return "\n".join(lines)


def output_tail(text: str, max_lines: int = 40, max_bytes: int = 8192) -> str:
    """The end of a tool's output: the last ``max_lines`` lines, and never
    more than ``max_bytes`` of them, so a 2 MB result costs 8 KB on the wire."""
    lines = text.splitlines()[-max_lines:]
    tail = "\n".join(lines)
    if len(tail.encode()) > max_bytes:
        tail = "…" + tail.encode()[-max_bytes:].decode("utf-8", "ignore")
    return tail


def money(usd: float) -> str:
    return f"${usd:.2f}" if usd >= 0.01 else f"${usd:.4f}"
