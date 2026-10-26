"""What a tool call did (its activity) and what the work served (its purpose).

Both are data, not logic: a new tool or harness is a row (spec 06). Every
harness's parser names its tools in Claude's vocabulary (``Bash``, ``Read``,
``mcp__aegis__...``), so one table serves them all. The first matching row
wins; a shell command matching nothing, and a tool no row names, is reading.
"""

from __future__ import annotations

import re

from ..journal.shell import _body, _commands, _git_dir, _skip_preamble

# Tool names, by full name, in order.
TOOLS: list[tuple[str, str]] = [
    (
        r"(.*__)?(turn_end|plan_update|session_rename|session_quiet|journal_\w+"
        r"|help_\w+|layout_\w+|meta|setup_record|config_\w+|models_list"
        r"|quota_read|agents_list)|ToolSearch|Skill|TodoWrite",
        "bookkeeping",
    ),
    (r"Read|Grep|Glob|LS|NotebookRead", "reading"),
    (r"Edit|MultiEdit|Write|NotebookEdit", "editing"),
    (
        r"(.*__)?(queue_\w+|peer_\w+|session_spawn|session_close|session_list"
        r"|workflow_\w+|task_\w+|monitor_sessions|schedule_\w+|notice_announce)"
        r"|Task|Agent",
        "other agents",
    ),
    (r"(.*__)?(monitor_\w+|browser_\w+)", "testing"),
    (r"(.*__)?(artifact_\w+|file_send|secret_\w+|toast)", "showing"),
    (r"WebSearch|WebFetch|(?i:.*(fetch|firecrawl|web_?search|scrape).*)", "web"),
]

# Shell commands, matched against each simple command with its wrappers
# (sudo, uv run, npx, ...) removed and git's options dropped.
SHELL: list[tuple[str, str]] = [
    (
        r"git (commit|push|cherry-pick|revert|merge)\b|gh pr (create|merge)\b",
        "committing",
    ),
    (
        r"(pytest|tox|nox|npm (run )?test|yarn test|pnpm test|playwright|ruff|mypy"
        r"|tsc|rift|cargo (test|check|clippy)|go (test|vet)|make (test|check|lint)\S*"
        r"|eslint|vitest|jest)\b|gh (run|pr checks)\b",
        "testing",
    ),
    (
        r"(ssh|scp|rsync|systemctl|journalctl|docker|podman|kubectl|apt|apt-get|dnf"
        r"|yum|brew|snap|hcloud)\b",
        "server",
    ),
    (r"(curl|wget)\b", "web"),  # to a non-local URL: _curl_web
]

WRAPPERS = re.compile(
    r"^(?:(?:sudo|env|time|nohup|uv run|uvx|npx|poetry run|python3? -m|timeout \S+)\s+)+"
)
# A PR read: review when nothing is edited after it in the turn.
PR_READ = re.compile(r"\bgh pr (view|diff|review)\b|\bgit (diff|show)\b")
LOCAL_URL = re.compile(r"^(https?://)?(localhost|127\.|0\.0\.0\.0|\[::1\])")

# Activities that serve the purpose of the work they lead to.
SUPPORT = {"reading", "testing", "bookkeeping", "showing"}

COMMIT_TYPES = {
    "feat": "build",
    "perf": "build",
    "fix": "fix",
    "test": "fix",
    "refactor": "refactor",
    "style": "refactor",
    "docs": "docs",
    "chore": "ops",
    "ci": "ops",
    "build": "ops",
}
CONVENTIONAL = re.compile(r"^(\w+)(\([^)]*\))?!?:")

DIRECT = {"web": "research", "server": "ops", "other agents": "coordinate"}


def _segments(command: str) -> list[str]:
    """Each simple command as one normalised line: wrappers dropped, a git
    command reduced to ``git <subcommand> ...``."""
    out = []
    for argv in _commands(_body(command)):
        argv = argv[_skip_preamble(argv) :]
        if not argv:
            continue
        if argv[0] == "git":
            _, sub = _git_dir(argv, "")
            rest = argv[argv.index(sub) + 1 :] if sub in argv else []
            argv = ["git", sub, *rest]
        out.append(WRAPPERS.sub("", " ".join(argv)))
    return out


def _curl_web(seg: str) -> bool:
    args = [w for w in seg.split()[1:] if not w.startswith("-")]
    return any(
        not LOCAL_URL.match(a) and re.match(r"^(https?://)?[\w.-]+\.[a-z]{2,}", a)
        for a in args
    )


def _shell(command: str) -> str:
    segs = _segments(command)
    for pattern, act in SHELL:
        for s in segs:
            if re.match(pattern, s) and (act != "web" or _curl_web(s)):
                return act
    return "reading"


def activity(tool: str, inp: dict) -> str:
    """What the tool call's hands did."""
    if tool == "Bash":
        return _shell(str(inp.get("command") or ""))
    for pattern, act in TOOLS:
        if re.fullmatch(pattern, tool):
            return act
    return "reading"


def edit_purpose(path: str) -> str | None:
    """The purpose an edit of this path has at once; None for code, which
    waits for its commit."""
    p = path.replace("\\", "/")
    name = p.rsplit("/", 1)[-1]
    if re.search(r"/docs/superpowers/(specs|plans)/", p) or "-design." in name:
        return "planning"
    if re.match(r"(README|AGENTS|CLAUDE|CHANGELOG|CONTRIBUTING)\b", name) or re.search(
        r"/(docs|help|know-how|changelog\.d)/", p
    ):
        return "docs"
    if re.search(r"\.(md|txt|tex|rst)$", name):
        return "writing"
    return None


def commit_purpose(subject: str) -> str | None:
    """The purpose a conventional commit subject names, None without a type."""
    m = CONVENTIONAL.match(subject.strip())
    return COMMIT_TYPES.get(m[1].lower()) if m else None


def direct_purpose(activity: str, tool: str, inp: dict) -> str | None:
    """The purpose a call has by itself: research, ops, coordinate, or review
    for a PR read (the deriver drops review when the turn edits later)."""
    if activity in DIRECT:
        return DIRECT[activity]
    if activity == "reading" and tool == "Bash":
        if PR_READ.search(_body(str(inp.get("command") or ""))):
            return "review"
    return None


def is_shell_exit(tool: str, output: str, is_error: bool) -> bool:
    """A shell command that exited non-zero, counted apart from tool errors."""
    return is_error and tool == "Bash" and output.lstrip().startswith("Exit code ")
