"""Which repo a transcript record is about, and how much of a session is one repo's.

A session carries no project label, so the only signal is what its records
mention. The measurement is locality: a session's share of a repo is the
fraction of its repo-mentioning records that name that one.

The path comparison here is the trap this module exists for. A substring test
on ``cwd`` matches ``/tmp/aegis-shot-a1b2`` and ``.aegis/state``; it once put
1,679 sessions on aegis where the real number was 394, and the output looked
entirely plausible. Compare resolved path components, and count the repo's git
worktrees (``<repo>-wt-*``) as the repo.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

_WT_RE = re.compile(r"-wt-[A-Za-z0-9_-]+$")

# A module is the first path segment, except inside a monorepo container,
# where it is the first two. Without this every app in apps/ collapses into
# one row called "apps".
SPLIT_DIRS = frozenset(
    {"apps", "packages", "services", "crates", "cmd", "modules", "libs"}
)

# Build artefacts and vendored trees appear in transcripts as often as source
# does, and attributing cost to .venv/ says nothing about where work went.
NOISE_DIRS = frozenset(
    {
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        "site",
        ".playground",
        "target",
        ".tox",
        "htmlcov",
        ".idea",
        ".vscode",
    }
)

BINARY_EXT = frozenset(
    {
        ".pdf",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".ico",
        ".svg",
        ".xlsx",
        ".xls",
        ".docx",
        ".pptx",
        ".odt",
        ".dxf",
        ".dwg",
        ".zip",
        ".gz",
        ".tar",
        ".whl",
        ".woff",
        ".woff2",
        ".ttf",
        ".otf",
        ".db",
        ".sqlite",
        ".sqlite3",
        ".mdb",
        ".accdb",
        ".bak",
        ".bin",
        ".so",
        ".dylib",
        ".mp3",
        ".m4a",
        ".wav",
        ".ogg",
        ".flac",
        ".mp4",
        ".mov",
    }
)
CODE_EXT = frozenset(
    {
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".go",
        ".rs",
        ".rb",
        ".java",
        ".kt",
        ".c",
        ".h",
        ".cc",
        ".cpp",
        ".hpp",
        ".cs",
        ".php",
        ".swift",
        ".sh",
        ".bash",
        ".zsh",
        ".sql",
        ".html",
        ".css",
        ".scss",
        ".vue",
        ".svelte",
    }
)
PROSE_EXT = frozenset({".md", ".rst", ".txt", ".qmd", ".tex", ".adoc"})

ROOT_MODULE = "(root)"
NO_MODULE = "(no module)"
WHOLE_REPO = "(whole repo)"


def mention_re(container: str) -> re.Pattern[str]:
    """The pattern that recognises a sibling repo named in a transcript record.

    ``container`` is the name of the directory the repos sit in — ``repos`` in
    this workspace, whatever it is elsewhere. It is a parameter and not the
    literal ``repos/`` it started as: aegis ships on PyPI and takes a path
    precisely because it has no such convention, and hard-coding one made every
    mention-only session score 0 for anyone with a different layout, with
    output that looked plausible.
    """
    return re.compile(rf"{re.escape(container)}/([A-Za-z0-9._-]+)")


def repo_roots(repo_path: Path) -> tuple[str, ...]:
    """The strings a cwd must start with to count as inside this repo.

    Resolved once here so a symlinked or trailing-slash path still matches.
    """
    resolved = str(Path(repo_path).resolve())
    return (resolved, resolved + "-wt-")


def cwd_inside(cwd: str | None, roots: tuple[str, ...]) -> bool:
    """True when ``cwd`` is the repo, inside it, or inside one of its worktrees."""
    if not cwd:
        return False
    repo, wt_prefix = roots
    if cwd == repo or cwd.startswith(repo + "/"):
        return True
    return cwd.startswith(wt_prefix)


def repos_mentioned(
    text: str, pattern: re.Pattern[str], fold: str | None = None
) -> set[str]:
    """Every ``<container>/<name>`` in one record, worktree suffixes folded away.

    ``pattern`` comes from :func:`mention_re`. ``fold`` names the repo under
    measurement: any mention starting with it (``aegis-wt-slice2``) folds onto
    it.
    """
    found: set[str] = set()
    for match in pattern.finditer(text):
        name = match.group(1)
        if fold and name.startswith(fold):
            name = fold
        else:
            name = _WT_RE.sub("", name)
        found.add(name)
    return found


def session_share(
    repo: str,
    repo_recs: Mapping[str, int],
    cwd: str | None,
    roots: tuple[str, ...],
) -> float:
    """One session's share of one repo, in [0, 1].

    A session already working inside the repo counts whole. Otherwise it is
    the fraction of its repo-mentioning records that name this one.
    """
    if cwd_inside(cwd, roots):
        return 1.0
    mine = repo_recs.get(repo, 0)
    other = sum(count for name, count in repo_recs.items() if name != repo)
    total = mine + other
    return mine / total if total else 0.0


def module_of(path: str, split_dirs: frozenset[str] = SPLIT_DIRS) -> str:
    parts = path.split("/")
    if len(parts) > 2 and parts[0] in split_dirs:
        return f"{parts[0]}/{parts[1]}"
    if len(parts) > 1:
        # A bare container mention names the group, not one of its members.
        return f"{parts[0]}/*" if parts[0] in split_dirs else parts[0]
    return ROOT_MODULE


def classify(path: str) -> str:
    """code / prose / data / binary. Trap 3 depends on this running first."""
    ext = Path(path).suffix.lower()
    if ext in BINARY_EXT:
        return "binary"
    if ext in CODE_EXT:
        return "code"
    if ext in PROSE_EXT:
        return "prose"
    return "data"
