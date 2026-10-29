"""What a tool call says about itself: glyph, label, result, args, diff.

Copied from the old tree's ``render_shared.py`` and trimmed to what slice 1
draws. Pure functions; the browser receives their output as entry fields and
never computes any of it (DESIGN.md, "Python decides, the browser draws").
"""

from __future__ import annotations

import difflib
import html
import json
import re

from .. import refs

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

# A glyph is the name of a symbol in the client's sprite (js/glyphs.js), drawn
# in the theme's colours; a character would render in whatever font the
# system falls back to, and an emoji in its own colours.
KIND_GLYPH = {
    "read": "book",
    "edit": "pencil",
    "execute": "terminal",
    "search": "search",
    "think": "think",
    "fetch": "globe",
    "other": "dot",
}

USER_GLYPH = "prompt"
PROSE_GLYPH = "dot"
THINKING_GLYPH = "think"
SYSTEM_GLYPH = "pip"
ERROR_GLYPH = "close"
COMMS_GLYPH = "swap"
FILE_GLYPH = "file"
ARTIFACT_GLYPH = "artifact"
SECRET_GLYPH = "lock"
KNOWLEDGE_GLYPH = "book"
COMMAND_GLYPH = "slash"
RECAP_GLYPH = "sparkle"
BTW_GLYPH = "btw"

# How much of a result the one-line verdict may carry.
DIGEST_MAX = 200


def _trunc(s: str, n: int) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _tail(path: str) -> str:
    return path.rsplit("/", 1)[-1] if path else ""


AEGIS_PREFIX = "mcp__aegis__"

# Lines Claude Code appends after a Bash call's own output. Left in, they
# would be the verdict of every call that ran ``cd`` or rewrote a read file.
CLAUDE_TRAILERS = ("Shell cwd was reset to ", "[This command modified ")


def _bash_comment(command: str) -> str:
    """The ``# what it does`` line the primer asks a command to open with.
    Claude Code drops the optional description field for whole sessions
    (14 of 81 in early October), and a comment in the command survives that."""
    first = command.lstrip().split("\n", 1)[0]
    return first.lstrip("#").strip() if first.startswith("#") else ""


def tool_glyph(name: str) -> str:
    if name.startswith(AEGIS_PREFIX):
        return COMMS_GLYPH
    return KIND_GLYPH[KIND_BY_NAME.get(name, "other")]


def tool_title(name: str) -> str:
    """A call to aegis is named by its verb; ``mcp__aegis__`` is noise."""
    return name.removeprefix(AEGIS_PREFIX)


def _aegis_label(verb: str, inp: dict) -> str:
    first = lambda v: next((ln.strip() for ln in str(v).splitlines() if ln.strip()), "")  # noqa: E731
    if verb == "monitor_start":
        return _trunc(inp.get("description", ""), 70)
    if verb == "queue_enqueue":
        return _trunc(f"{inp.get('queue', '')}: {first(inp.get('payload', ''))}", 70)
    if verb in ("peer_handoff", "peer_read"):
        return str(inp.get("target", ""))
    for v in inp.values():
        if isinstance(v, str) and v.strip():
            return _trunc(v, 60)
    return ""


def tool_label(name: str, inp: dict) -> str:
    """The shortest honest name for a call: which call it was, without its
    arguments, except a search's pattern, which is which search it was."""
    if name.startswith(AEGIS_PREFIX):
        return _aegis_label(tool_title(name), inp)
    if name == "Bash":
        if comment := _bash_comment(str(inp.get("command", ""))):
            return comment
        if inp.get("description"):
            return str(inp["description"])
        return _trunc(inp.get("command", ""), 60)
    if name in FILE_TOOLS:
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


def _aegis_digest(text: str) -> str:
    """An aegis call's reply, as the one fact a row needs: the id it made, the
    status it reports, or how many things it listed."""
    try:
        v = json.loads(text)
    except ValueError:
        first = next((ln for ln in text.splitlines() if ln.strip()), "ok")
        return _trunc(first, DIGEST_MAX)
    if isinstance(v, list):
        return f"{len(v)} item{'s' if len(v) != 1 else ''}"
    if isinstance(v, dict):
        parts = [
            str(v[k])
            for k in ("monitor_id", "task_id", "cancelled", "status", "handle")
            if v.get(k)
        ]
        return _trunc(" · ".join(parts) or "ok", DIGEST_MAX)
    return _trunc(str(v), DIGEST_MAX)


def diff_counts(old_text: str, new_text: str) -> tuple[int, int]:
    added = removed = 0
    for line in difflib.ndiff(old_text.splitlines(), new_text.splitlines()):
        if line.startswith("+ "):
            added += 1
        elif line.startswith("- "):
            removed += 1
    return added, removed


def diff_hunks(old: str, new: str, context: int = 3) -> list[dict]:
    """The edit's hunks, ``context`` unchanged lines around each change, lines
    prefixed with a space, ``-`` or ``+``; numbered within the edit's text."""
    a, b = old.splitlines(), new.splitlines()
    out = []
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for group in matcher.get_grouped_opcodes(context):
        lines: list[str] = []
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                lines += [" " + x for x in a[i1:i2]]
            if tag in ("replace", "delete"):
                lines += ["-" + x for x in a[i1:i2]]
            if tag in ("replace", "insert"):
                lines += ["+" + x for x in b[j1:j2]]
        out.append({"a": group[0][1] + 1, "b": group[0][3] + 1, "lines": lines})
    return out


def hunks_from_patch(patch: list) -> list[dict]:
    """Claude Code's ``structuredPatch`` as hunks; its marker lines
    (``\\ No newline at end of file``) dropped."""
    return [
        {
            "a": int(h["oldStart"]),
            "b": int(h["newStart"]),
            "lines": [
                str(x) for x in h.get("lines", []) if str(x)[:1] in (" ", "-", "+")
            ],
        }
        for h in patch
        if isinstance(h, dict) and "oldStart" in h and "newStart" in h
    ]


def cap_hunks(hunks: list[dict], max_bytes: int = 8192) -> tuple[list[dict], int]:
    """Lines in order up to ``max_bytes``, the same cap as ``output_tail``;
    the changed lines after the cap are counted, not kept."""
    kept, size, elided, full = [], 0, 0, False
    for h in hunks:
        lines = []
        for ln in h["lines"]:
            cost = len(ln.encode()) + 1
            if full or size + cost > max_bytes:
                full = True
                elided += ln.startswith(("+", "-"))
                continue
            size += cost
            lines.append(ln)
        if lines:
            kept.append({**h, "lines": lines})
    return kept, elided


FILE_TOOLS = ("Read", "Write", "Edit", "MultiEdit", "NotebookEdit")


def file_path(name: str, inp: dict) -> str | None:
    """The file a file tool read or changed, else None."""
    path = inp.get("file_path") or inp.get("notebook_path")
    return path if name in FILE_TOOLS and isinstance(path, str) else None


# The highlight.js language (client/js/highlight.js LANGS) a file's name implies.
LANG_BY_EXT = {
    "sh": "bash", "bash": "bash", "zsh": "bash", "py": "python", "pyi": "python",
    "js": "javascript", "mjs": "javascript", "cjs": "javascript", "jsx": "javascript",
    "ts": "typescript", "tsx": "typescript", "json": "json", "jsonl": "json",
    "yaml": "yaml", "yml": "yaml", "toml": "ini", "ini": "ini", "cfg": "ini",
    "diff": "diff", "patch": "diff", "md": "markdown", "css": "css", "html": "xml",
    "xml": "xml", "svg": "xml", "sql": "sql", "rs": "rust", "go": "go", "c": "c",
    "h": "c", "cc": "cpp", "cpp": "cpp", "hpp": "cpp", "java": "java", "lua": "lua",
    "rb": "ruby", "php": "php", "kt": "kotlin", "swift": "swift", "mk": "makefile",
}  # fmt: skip
LANG_BY_NAME = {
    "makefile": "makefile",
    "dockerfile": "dockerfile",
    "nginx.conf": "nginx",
}


def lang_of_path(path: str) -> str | None:
    name = _tail(path).lower()
    if name in LANG_BY_NAME:
        return LANG_BY_NAME[name]
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    return LANG_BY_EXT.get(ext)


def tool_lang(name: str, inp: dict) -> str | None:
    """The language a tool row's views draw in: Bash's command, a file tool's
    file, every other tool's ``key: value`` arguments (#323)."""
    if name in ("Bash", "BashOutput"):
        return "bash"
    if path := file_path(name, inp):
        return lang_of_path(path)
    return "yaml"


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
    if name.startswith(AEGIS_PREFIX) and not is_error:
        return _aegis_digest(text)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if name == "Bash":
        while len(lines) > 1 and lines[-1].startswith(CLAUDE_TRAILERS):
            lines.pop()
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
        has = inp.get("description") and not _bash_comment(str(inp["command"]))
        out = [f"# {inp['description']}"] if has else []
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


_NUMBERED = re.compile(r"^\s*(\d+)\t(.*)$")


def numbered_lines(text: str) -> tuple[int, list[str]] | None:
    """Claude Code's Read output (``     7\\tline``): the first line's number and
    the lines without their numbers, up to the first line that is not numbered
    (the harness appends notes after the file). None when it is not that shape."""
    first, out = None, []
    for ln in text.splitlines():
        m = _NUMBERED.match(ln)
        if not m:
            if out:
                break
            continue
        if first is None:
            first = int(m.group(1))
        out.append(m.group(2))
    return (first, out) if first is not None else None


def file_window(first: int, lines: list[str]) -> dict:
    """The end of a file view, as much as ``output_tail`` keeps, and the
    number of the first line kept."""
    text = output_tail("\n".join(lines))
    return {"first": first + len(lines) - len(text.splitlines()), "text": text}


_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_SPAN = re.compile(r"(`+)([^`\n]+?)\1")


def inline_paths(md: str) -> list[str]:
    """The inline code spans of a reply that may name a file, a folder or a
    GitHub repo (refs.candidate), with an optional ``:line``, in order and
    without repeats; fenced blocks skipped. What each one names is asked when
    the reply is shown (path.stat, #393); a click asks again (#323)."""
    out: list[str] = []
    fence = None
    for line in md.splitlines():
        if m := _FENCE.match(line):
            mark = m.group(1)
            if fence is None:
                fence = mark
            elif mark[0] == fence[0] and len(mark) >= len(fence):
                fence = None
            continue
        if fence is not None:
            continue
        for span in _SPAN.finditer(line):
            text = span.group(2).strip()
            if refs.candidate(text) and text not in out:
                out.append(text)
    return out


def money(usd: float) -> str:
    return f"${usd:.2f}" if usd >= 0.005 else f"${usd:.4f}"


_TASK_NOTICE = re.compile(r"<task-notification>(.*?)</task-notification>", re.S)
_TASK_SUMMARY = re.compile(r'^(Background command|Agent) "(.*)" ([^"]*)$', re.S)
_EXIT_CODE = re.compile(r"exit code (-?\d+)")
TASK_FAILED = ("failed", "killed")


def _tag(block: str, name: str) -> str:
    m = re.search(rf"<{name}>(.*?)</{name}>", block, re.S)
    return html.unescape(m.group(1)).strip() if m else ""


def task_notices(text: str) -> list[dict]:
    """The notices Claude Code sends as a prompt when a background command or
    a subagent ends, one dict each, or none when the text is not only that.
    Its ids are kept out: they name nothing the person can act on."""
    blocks = _TASK_NOTICE.findall(text)
    if not blocks or _TASK_NOTICE.sub("", text).strip():
        return []
    out = []
    for block in blocks:
        status = _tag(block, "status") or "ended"
        summary = _tag(block, "summary")
        m = _TASK_SUMMARY.match(summary)
        source = "subagent" if m and m.group(1) == "Agent" else "background"
        what, tail = (m.group(2), m.group(3)) if m else (summary, summary)
        header = [source, status]
        if code := _EXIT_CODE.search(tail):
            header.append(f"exit {code.group(1)}")
        elif "time limit" in tail:
            header.append("time limit")
        detail: dict = {"output": _tag(block, "output-file")}
        if source == "subagent":
            detail["fold"] = _tag(block, "result")
            label = what
        else:
            detail |= {"fold": what, "lang": "bash"}
            label = _bash_comment(what) or what.split("\n", 1)[0]
            if label == what:  # a description, or a one-line command
                detail = {"output": detail["output"]}
        out.append(
            {
                "status": "err" if status in TASK_FAILED else "ok",
                "title": " · ".join(header),
                "md": label,
                "detail": {k: v for k, v in detail.items() if v},
            }
        )
    return out
