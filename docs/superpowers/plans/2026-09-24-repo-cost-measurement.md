# Repo cost measurement implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `aegis usage repo <path>` and `aegis usage repos <dir>`, which
measure what a repository cost to build by joining agent transcripts with git
history and attributing cost to the repo by locality.

**Architecture:** A new package `src/aegis/cost/` holds four pure-ish layers:
`locality` (which repos a record mentions, and one session's share of one repo),
`gitfacts` (commits, churn, lines in the tree, classified), `scan` (the four
transcript stores, deduplicated, two reading paths), and `measure` (assembly into
a `RepoCost`). `render` prints English tables. `cli_usage.py` gains two
subcommands; `mcp/server.py` gains one read-only tool over the JSON cache.

**Tech Stack:** Python 3.13, typer, `aegis.models` price registry, `aegis.usage.cost.token_cost`, pytest.

**Spec:** `docs/superpowers/specs/2026-09-24-repo-cost-measurement-design.md`

## Global constraints

- Python 3.13+, `uv` only, never pip.
- English in code, comments, docstrings, commit messages and `docs/`.
- Shared checkout: `git commit -- <paths>` with explicit paths, never `git add -A`, never `--amend`. Work on `main`.
- Every test must finish under 3 seconds or carry `@pytest.mark.slow` (`tests/conftest.py` enforces it via `--max-unmarked-duration=3`).
- Prices come from `aegis.models.get_prices` through `aegis.usage.aggregate.resolve_prices`. No hardcoded price table.
- A 1-hour cache write is priced at twice the 5-minute rate (`CACHE_1H_MULT = 2`), because `ProviderPrices` carries one `cache_write` field.
- Never resolve an off-host path against the local disk (`DESIGN.md`).
- `make check` verbatim before claiming done; the daemon must be restarted after the change before any user-facing claim.

## Review focus

Five input classes the spec implies that no task's headline test would otherwise reach. Each has a test folded into the task that owns the code.

1. A repo path that is a symlink or has a trailing slash. `Path.resolve()` must run once at entry so locality compares resolved components; otherwise every session scores 0. Covered in Task 1, Step 6.
2. A transcript line that is truncated JSON. A damaged file must never take the scan down (`DESIGN.md`: "a damaged file never takes a session down"). Covered in Task 3, Step 8.
3. A repo with zero commits in the window. Division by `n_commits` must not raise. Covered in Task 4, Step 6.
4. A `--since` later than every transcript. The report must print zero cost and 100% coverage rather than a negative or a crash. Covered in Task 2, Step 8.
5. A `<dir>` for `aegis usage repos` holding a non-git subdirectory. The sweep must skip it and keep going rather than abort the other 79 repos. Covered in Task 5, Step 8.

---

## File structure

| file | responsibility |
|---|---|
| `src/aegis/cost/__init__.py` | public names: `RepoCost`, `measure`, `sweep`, `CostOptions` |
| `src/aegis/cost/locality.py` | pure: repo mentions, path classification, module naming, one session's share |
| `src/aegis/cost/gitfacts.py` | `git log` and `ls-files` parsing, classified churn and lines, coverage |
| `src/aegis/cost/scan.py` | the four transcript stores, deduplication, per-session token accumulation |
| `src/aegis/cost/measure.py` | `CostOptions`, `RepoCost`, `measure()`, `sweep()` |
| `src/aegis/cost/render.py` | English terminal tables for one repo and for a sweep |
| `src/aegis/cli_usage.py` | the `repo` and `repos` subcommands |
| `src/aegis/mcp/server.py` | `aegis_repo_cost` tool |
| `tests/test_cost_locality.py` | traps 1, module naming, classification |
| `tests/test_cost_gitfacts.py` | traps 2, 3, 4, 5 against a real temporary git repo |
| `tests/test_cost_scan.py` | trap 6, deduplication, damaged lines |
| `tests/test_cost_measure.py` | bands, strict vs proportional, JSON shape |
| `tests/test_cost_cli.py` | both subcommands end to end over a fixture tree |

---

### Task 1: Locality

**Files:**
- Create: `src/aegis/cost/__init__.py`, `src/aegis/cost/locality.py`
- Test: `tests/test_cost_locality.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `repo_roots(repo_path: Path) -> tuple[str, ...]` — the resolved repo path and its worktree prefix.
  - `cwd_inside(cwd: str | None, roots: tuple[str, ...]) -> bool`
  - `repos_mentioned(text: str, fold: str | None = None) -> set[str]`
  - `session_share(repo: str, repo_recs: Mapping[str, int], cwd: str | None, roots: tuple[str, ...]) -> float`
  - `module_of(path: str, split_dirs: frozenset[str]) -> str`
  - `classify(path: str) -> str` returning `"code" | "prose" | "data" | "binary"`
  - `SPLIT_DIRS: frozenset[str]`, `NOISE_DIRS: frozenset[str]`

- [ ] **Step 1: Write the failing test for trap 1**

```python
# tests/test_cost_locality.py
from pathlib import Path

from aegis.cost.locality import cwd_inside, repo_roots


def test_cwd_substring_does_not_count_as_the_repo(tmp_path):
    """Trap 1. `"aegis" in cwd` matched /tmp/aegis-shot-a1b2 and .aegis/state
    and put 1,679 sessions on aegis where the real number was 394."""
    repo = tmp_path / "repos" / "aegis"
    repo.mkdir(parents=True)
    roots = repo_roots(repo)

    assert cwd_inside(str(repo), roots) is True
    assert cwd_inside(str(repo / "src" / "aegis"), roots) is True
    assert cwd_inside(str(tmp_path / "repos" / "aegis-wt-slice2"), roots) is True

    assert cwd_inside("/tmp/aegis-shot-a1b2", roots) is False
    assert cwd_inside(str(tmp_path / ".aegis" / "state"), roots) is False
    assert cwd_inside(str(tmp_path / "repos" / "aegisx"), roots) is False
    assert cwd_inside(None, roots) is False
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_cost_locality.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'aegis.cost'`.

- [ ] **Step 3: Write `locality.py`**

```python
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

REPO_RE = re.compile(r"repos/([A-Za-z0-9._-]+)")
_WT_RE = re.compile(r"-wt-[A-Za-z0-9_-]+$")

# A module is the first path segment, except inside a monorepo container,
# where it is the first two. Without this every app in apps/ collapses into
# one row called "apps".
SPLIT_DIRS = frozenset({"apps", "packages", "services", "crates", "cmd", "modules", "libs"})

# Build artefacts and vendored trees appear in transcripts as often as source
# does, and attributing cost to .venv/ says nothing about where work went.
NOISE_DIRS = frozenset({
    ".venv", "venv", "node_modules", "__pycache__", ".git", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "dist", "build", "site", ".playground",
    "target", ".tox", "htmlcov", ".idea", ".vscode",
})

BINARY_EXT = frozenset({
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg",
    ".xlsx", ".xls", ".docx", ".pptx", ".odt", ".dxf", ".dwg",
    ".zip", ".gz", ".tar", ".whl", ".woff", ".woff2", ".ttf", ".otf",
    ".db", ".sqlite", ".sqlite3", ".mdb", ".accdb", ".bak", ".bin", ".so", ".dylib",
    ".mp3", ".m4a", ".wav", ".ogg", ".flac", ".mp4", ".mov",
})
CODE_EXT = frozenset({
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".go", ".rs", ".rb",
    ".java", ".kt", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".php", ".swift",
    ".sh", ".bash", ".zsh", ".sql", ".html", ".css", ".scss", ".vue", ".svelte",
})
PROSE_EXT = frozenset({".md", ".rst", ".txt", ".qmd", ".tex", ".adoc"})

ROOT_MODULE = "(root)"
NO_MODULE = "(no module)"
WHOLE_REPO = "(whole repo)"


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


def repos_mentioned(text: str, fold: str | None = None) -> set[str]:
    """Every ``repos/<name>`` in one record, worktree suffixes folded away.

    ``fold`` names the repo under measurement: any mention starting with it
    (``aegis-wt-slice2``) folds onto it.
    """
    found: set[str] = set()
    for match in REPO_RE.finditer(text):
        name = match.group(1)
        if fold and name.startswith(fold):
            name = fold
        else:
            name = _WT_RE.sub("", name)
        found.add(name)
    return found


def session_share(
    repo: str, repo_recs: Mapping[str, int], cwd: str | None, roots: tuple[str, ...]
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
```

And `src/aegis/cost/__init__.py`:

```python
"""What a repository cost to build, measured.

Backs ``aegis usage repo``. The design, the attribution method and the six
traps it defends against are in
``docs/superpowers/specs/2026-09-24-repo-cost-measurement-design.md``.
"""

from __future__ import annotations
```

- [ ] **Step 4: Run the test and watch it pass**

Run: `uv run pytest tests/test_cost_locality.py -q`
Expected: PASS.

- [ ] **Step 5: Add the remaining locality tests**

```python
from aegis.cost.locality import classify, module_of, repos_mentioned, session_share


def test_worktree_mentions_fold_onto_the_repo():
    text = "editing repos/aegis-wt-slice2/src/x.py and repos/une-tools/app.py"
    assert repos_mentioned(text, fold="aegis") == {"aegis", "une-tools"}
    assert repos_mentioned(text) == {"aegis", "une-tools"}


def test_share_is_the_record_ratio_when_cwd_is_elsewhere():
    roots = ("/nowhere", "/nowhere-wt-")
    assert session_share("aegis", {"aegis": 3, "other": 1}, "/home/x", roots) == 0.75
    assert session_share("aegis", {"other": 4}, "/home/x", roots) == 0.0
    assert session_share("aegis", {}, "/home/x", roots) == 0.0


def test_share_is_whole_when_the_session_works_inside_the_repo():
    roots = ("/w/repos/aegis", "/w/repos/aegis-wt-")
    assert session_share("aegis", {"other": 9}, "/w/repos/aegis/src", roots) == 1.0


def test_module_splits_monorepo_containers_but_not_ordinary_dirs():
    assert module_of("src/aegis/cli.py") == "src"
    assert module_of("apps/web/main.ts") == "apps/web"
    assert module_of("apps") == "(root)"
    assert module_of("apps/web") == "apps/*"
    assert module_of("README.md") == "(root)"


def test_classify_separates_binaries_from_text():
    assert classify("docs/report.pdf") == "binary"
    assert classify("docs/report.md") == "prose"
    assert classify("src/main.py") == "code"
    assert classify("data/rows.csv") == "data"
```

- [ ] **Step 6: Add the Review-focus test for a symlinked repo path**

```python
def test_repo_roots_resolves_symlinks_and_trailing_slashes(tmp_path):
    real = tmp_path / "real" / "aegis"
    real.mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "real")

    assert repo_roots(link / "aegis") == repo_roots(real)
    assert repo_roots(Path(str(real) + "/")) == repo_roots(real)
    assert cwd_inside(str(real / "src"), repo_roots(link / "aegis")) is True
```

- [ ] **Step 7: Run the file and commit**

```bash
uv run pytest tests/test_cost_locality.py -q
git commit -- src/aegis/cost/__init__.py src/aegis/cost/locality.py tests/test_cost_locality.py \
  -m "feat(cost): locality attribution for repo cost measurement"
```

---

### Task 2: Git facts

**Files:**
- Create: `src/aegis/cost/gitfacts.py`
- Test: `tests/test_cost_gitfacts.py`

**Interfaces:**
- Consumes: `aegis.cost.locality.{classify, module_of, SPLIT_DIRS}`.
- Produces:
  - `GitFacts` dataclass with fields `dates: list[str]`, `n_commits: int`, `first: str | None`, `last: str | None`, `active_days: int`, `weeks: dict[str, dict[str, int]]`, `types: dict[str, dict[str, int]]`, `authors: dict[str, dict[str, int]]`, `churn: dict[str, dict[str, int]]`, `loc: dict[str, dict[str, int]]`, `langs: dict[str, dict[str, int]]`, `excluded: tuple[str, ...]`.
  - `git_facts(repo_path: Path, *, since: str | None, until: str | None, split_dirs: frozenset[str], exclude: tuple[str, ...]) -> GitFacts`
  - `coverage(dates: list[str], first_seen: str | None) -> float`

- [ ] **Step 1: Write the failing test for trap 2**

```python
# tests/test_cost_gitfacts.py
import subprocess
from pathlib import Path

import pytest

from aegis.cost.gitfacts import coverage, git_facts
from aegis.cost.locality import SPLIT_DIRS


def _git(repo: Path, *args: str) -> None:
    subprocess.run(("git", *args), cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "sample"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "Tester")
    return r


def _commit(repo: Path, message: str, files: dict[str, str], date: str) -> None:
    for name, body in files.items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
        _git(repo, "add", "--", name)
    env_date = f"{date}T12:00:00+00:00"
    subprocess.run(
        ("git", "commit", "-q", "-m", message),
        cwd=repo, check=True, capture_output=True,
        env={"GIT_AUTHOR_DATE": env_date, "GIT_COMMITTER_DATE": env_date,
             "PATH": "/usr/bin:/bin", "HOME": str(repo),
             "GIT_AUTHOR_NAME": "Tester", "GIT_AUTHOR_EMAIL": "t@example.com",
             "GIT_COMMITTER_NAME": "Tester", "GIT_COMMITTER_EMAIL": "t@example.com"},
    )


def test_record_separator_in_a_subject_does_not_swallow_commits(repo):
    """Trap 2. Parsing with \\x1e and str.splitlines() returns zero commits,
    silently, because Python also breaks lines on \\x1c-\\x1e."""
    _commit(repo, "feat: first", {"a.py": "x = 1\n"}, "2026-06-01")
    _commit(repo, "fix: a \x1e b \x1c c", {"b.py": "y = 2\n"}, "2026-06-02")
    _commit(repo, "docs: third", {"c.md": "hello\n"}, "2026-06-03")

    facts = git_facts(repo, since=None, until=None, split_dirs=SPLIT_DIRS, exclude=())

    assert facts.n_commits == 3
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_cost_gitfacts.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'aegis.cost.gitfacts'`.

- [ ] **Step 3: Write `gitfacts.py`**

```python
"""What git says was built, classified so a PDF is never a line of code.

Two defects live here permanently, both paid for once:

``str.splitlines()`` also breaks on \\x1c-\\x1e, so a ``git log`` format using
\\x1e as a record separator parses to zero commits without an error. The
separator is \\x01 and the split is ``split("\\n")``.

``--numstat`` counts binaries as lines. Two PDFs in ``docs/`` were 62,074
"lines added" in one week, which made that week the most productive of the
project. Every path is classified before anything is added up.
"""

from __future__ import annotations

import collections
import fnmatch
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from aegis.cost.locality import SPLIT_DIRS, classify, module_of

SEP = "\x01"
_CONVENTIONAL = re.compile(r"^(\w+)(\([^)]*\))?!?:")


@dataclass
class GitFacts:
    dates: list[str] = field(default_factory=list)
    n_commits: int = 0
    first: str | None = None
    last: str | None = None
    active_days: int = 0
    weeks: dict[str, dict[str, int]] = field(default_factory=dict)
    types: dict[str, dict[str, int]] = field(default_factory=dict)
    authors: dict[str, dict[str, int]] = field(default_factory=dict)
    churn: dict[str, dict[str, int]] = field(default_factory=dict)
    loc: dict[str, dict[str, int]] = field(default_factory=dict)
    langs: dict[str, dict[str, int]] = field(default_factory=dict)
    excluded: tuple[str, ...] = ()


def _sh(repo_path: Path, *args: str) -> str:
    proc = subprocess.run(
        ("git", "-C", str(repo_path), *args), capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()[:400]}")
    return proc.stdout


def _dropped(path: str, exclude: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in exclude)


def coverage(dates: list[str], first_seen: str | None) -> float:
    """Share of commits inside the window the transcripts actually cover.

    Git counts every commit a repo ever had; cost only exists from the oldest
    surviving transcript. Without this the two halves of the answer describe
    different questions and the oldest repos come out looking free.
    """
    if not dates:
        return 1.0
    if not first_seen:
        return 0.0
    cut = first_seen[:10]
    return sum(1 for d in dates if d >= cut) / len(dates)


def git_facts(
    repo_path: Path,
    *,
    since: str | None = None,
    until: str | None = None,
    split_dirs: frozenset[str] = SPLIT_DIRS,
    exclude: tuple[str, ...] = (),
) -> GitFacts:
    window: list[str] = []
    if since:
        window.append(f"--since={since}")
    if until:
        window.append(f"--until={until} 23:59:59")
    raw = _sh(
        repo_path, "log", "--all", "--no-merges", "--date=iso-strict",
        f"--format=%H{SEP}%ad{SEP}%an{SEP}%s", "--numstat", *window,
    )

    commits: list[dict] = []
    current: dict | None = None
    # NB: split on "\n", never splitlines(). See the module docstring.
    for line in raw.split("\n"):
        if SEP in line:
            if current:
                commits.append(current)
            sha, date, author, subject = line.split(SEP, 3)
            current = {"sha": sha, "date": date, "author": author,
                       "subject": subject, "files": []}
        elif line.strip() and current is not None:
            parts = line.split("\t")
            if len(parts) == 3:
                ins, dele, path = parts
                if _dropped(path, exclude):
                    continue
                current["files"].append(
                    (int(ins) if ins.isdigit() else 0,
                     int(dele) if dele.isdigit() else 0, path)
                )
    if current:
        commits.append(current)

    weeks: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    types: collections.Counter = collections.Counter()
    type_ins: collections.Counter = collections.Counter()
    authors: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    churn: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    days: set[str] = set()
    per_week_days: dict[str, set[str]] = collections.defaultdict(set)
    per_module_files: dict[str, set[str]] = collections.defaultdict(set)
    per_module_commits: dict[str, set[str]] = collections.defaultdict(set)

    for commit in commits:
        at = datetime.fromisoformat(commit["date"])
        week = at.strftime("%G-W%V")
        day = at.strftime("%Y-%m-%d")
        days.add(day)
        per_week_days[week].add(day)
        match = _CONVENTIONAL.match(commit["subject"])
        kind = match.group(1).lower() if match else "other"
        types[kind] += 1
        weeks[week]["commits"] += 1
        author = authors[commit["author"]]
        author["commits"] += 1
        for ins, dele, path in commit["files"]:
            kind_of_file = classify(path)
            module = module_of(path, split_dirs)
            per_module_files[module].add(path)
            per_module_commits[module].add(commit["sha"])
            author["ins"] += ins
            author["dele"] += dele
            churn[module]["ins"] += ins
            churn[module]["dele"] += dele
            if kind_of_file == "binary":
                weeks[week]["bin"] += ins
                continue
            type_ins[kind] += ins
            weeks[week]["text"] += ins
            weeks[week]["text_del"] += dele
            weeks[week][kind_of_file] += ins
            if kind_of_file == "code":
                weeks[week]["code_del"] += dele

    for week, seen in per_week_days.items():
        weeks[week]["days"] = len(seen)
    for module, seen in per_module_files.items():
        churn[module]["files"] = len(seen)
        churn[module]["commits"] = len(per_module_commits[module])

    loc: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    langs: collections.Counter = collections.Counter()
    lang_files: collections.Counter = collections.Counter()
    for name in (f for f in _sh(repo_path, "ls-files", "-z").split("\0") if f):
        if _dropped(name, exclude):
            continue
        path = repo_path / name
        if not path.is_file():
            continue
        module = module_of(name, split_dirs)
        if classify(name) == "binary":
            loc[module]["binaries"] += 1
            continue
        try:
            lines = sum(1 for _ in path.open("rb"))
        except OSError:
            lines = 0
        loc[module]["files"] += 1
        loc[module][classify(name)] += lines
        ext = path.suffix.lower() or "(no extension)"
        langs[ext] += lines
        lang_files[ext] += 1

    return GitFacts(
        dates=sorted(datetime.fromisoformat(c["date"]).strftime("%Y-%m-%d")
                     for c in commits),
        n_commits=len(commits),
        first=commits[-1]["date"] if commits else None,
        last=commits[0]["date"] if commits else None,
        active_days=len(days),
        weeks={k: dict(v) for k, v in sorted(weeks.items())},
        types={k: {"commits": v, "ins": type_ins[k]} for k, v in types.most_common()},
        authors={k: dict(v) for k, v in
                 sorted(authors.items(), key=lambda kv: -kv[1]["commits"])},
        churn={k: dict(v) for k, v in
               sorted(churn.items(), key=lambda kv: -kv[1]["ins"])},
        loc={k: dict(v) for k, v in
             sorted(loc.items(), key=lambda kv: -kv[1]["code"])},
        langs={k: {"lines": v, "files": lang_files[k]} for k, v in langs.most_common()},
        excluded=exclude,
    )
```

- [ ] **Step 4: Run the trap-2 test and watch it pass**

Run: `uv run pytest tests/test_cost_gitfacts.py -q`
Expected: PASS.

- [ ] **Step 5: Add the trap-3 test (binaries are not lines)**

```python
def test_a_binary_never_counts_as_a_line_of_text(repo):
    """Trap 3. Two PDFs in docs/ were 62,074 "lines added" in one week."""
    _commit(repo, "feat: code", {"src/main.py": "a = 1\nb = 2\n"}, "2026-06-01")
    _commit(repo, "docs: report", {"docs/report.pdf": "\n".join("x" * 4 for _ in range(500))},
            "2026-06-02")

    facts = git_facts(repo, since=None, until=None, split_dirs=SPLIT_DIRS, exclude=())
    week = facts.weeks["2026-W23"]

    assert week["bin"] >= 500
    assert week.get("text", 0) == 0
    assert week.get("prose", 0) == 0
    assert facts.loc["docs"]["binaries"] == 1
    assert facts.loc["docs"].get("prose", 0) == 0
```

- [ ] **Step 6: Add the trap-5 test (a report excludes itself)**

```python
def test_exclude_drops_a_path_from_churn_and_from_the_tree(repo):
    """Trap 5. A report that lives in the repo counts itself on the next run."""
    _commit(repo, "feat: code", {"src/main.py": "a = 1\n"}, "2026-06-01")
    _commit(repo, "docs: cost report",
            {"docs/cost-report.md": "\n".join(f"line {i}" for i in range(498))},
            "2026-06-02")

    with_report = git_facts(repo, since=None, until=None, split_dirs=SPLIT_DIRS, exclude=())
    without = git_facts(repo, since=None, until=None, split_dirs=SPLIT_DIRS,
                        exclude=("docs/cost-report.md",))

    assert with_report.loc["docs"]["prose"] == 498
    assert "docs" not in without.loc
    assert "docs" not in without.churn
    assert without.excluded == ("docs/cost-report.md",)
    # the commit itself still exists; only its lines are dropped
    assert without.n_commits == 2
```

- [ ] **Step 7: Add the trap-4 test (coverage)**

```python
def test_coverage_is_the_share_of_commits_inside_the_transcript_window():
    """Trap 4. 93% of beaver's commits fell outside the 2026-05-29 floor and
    the repo came out looking free."""
    dates = ["2026-01-01", "2026-02-01", "2026-06-01", "2026-06-02"]

    assert coverage(dates, "2026-05-29T00:00:00Z") == 0.5
    assert coverage(dates, None) == 0.0
    assert coverage([], "2026-05-29T00:00:00Z") == 1.0
    assert coverage(dates, "2020-01-01T00:00:00Z") == 1.0
```

- [ ] **Step 8: Add the Review-focus test for an empty window**

```python
def test_a_window_after_every_commit_yields_no_commits_and_full_coverage(repo):
    _commit(repo, "feat: code", {"src/main.py": "a = 1\n"}, "2026-06-01")

    facts = git_facts(repo, since="2027-01-01", until=None,
                      split_dirs=SPLIT_DIRS, exclude=())

    assert facts.n_commits == 0
    assert facts.dates == []
    assert facts.first is None and facts.last is None
    assert coverage(facts.dates, "2026-05-29T00:00:00Z") == 1.0
```

- [ ] **Step 9: Run the file and commit**

```bash
uv run pytest tests/test_cost_gitfacts.py -q
git commit -- src/aegis/cost/gitfacts.py tests/test_cost_gitfacts.py \
  -m "feat(cost): classified git facts with coverage and path exclusion"
```

---

### Task 3: The transcript scanner

**Files:**
- Create: `src/aegis/cost/scan.py`
- Test: `tests/test_cost_scan.py`

**Interfaces:**
- Consumes: `aegis.cost.locality.{repos_mentioned, module_of, NOISE_DIRS, NO_MODULE}`, `aegis.usage.aggregate.resolve_prices`, `aegis.usage.cost.token_cost`.
- Produces:
  - `SessionScan` dataclass: `key: str`, `source: str`, `cwd: str | None`, `first_ts: str | None`, `last_ts: str | None`, `n_records: int`, `repo_records: Counter`, `modules: Counter`, `usage: dict[tuple[str, str], Counter]`, `timestamps: list[str]`.
  - `Scanner(repo: str | None, repo_path: Path, *, since, until, split_dirs)` with `.sessions: dict[str, SessionScan]`, `.first_seen: str | None`, `.seen: set[str]`, `.set_bare_modules(list[str])`, `.run(claude_roots, state_dir, foreign=True)`.
  - `active_hours(timestamps: list[str], cap: int = 300) -> dict[str, float]`

- [ ] **Step 1: Write the failing test for trap 6**

```python
# tests/test_cost_scan.py
import json
from pathlib import Path

from aegis.cost.locality import SPLIT_DIRS
from aegis.cost.scan import Scanner


def _ev(ts: str, **event) -> str:
    return json.dumps({"v": 1, "aegis_ts": ts, "event": event})


def _state_with_acp_session(tmp_path: Path, repo_path: Path) -> Path:
    state = tmp_path / ".aegis" / "state"
    (state / "sessions").mkdir(parents=True)
    (state / "sessions" / "happy-hellman.jsonl").write_text("\n".join([
        _ev("2026-08-05T17:05:14.000000Z", t="SessionMeta", handle="happy-hellman",
            provider="opencode", cwd=str(repo_path)),
        # The trap: a message id is present, usage is null.
        _ev("2026-08-05T17:06:00.000000Z", t="AssistantText", text="Yes",
            usage=None, message_id="msg_fd2e5cf2c001mIG1UNoXDniZW6"),
        _ev("2026-08-05T17:12:20.000000Z", t="Result", duration_ms=426134,
            is_error=False, cost_usd=0.0,
            usage={"input": 91, "cache_creation": 0, "cache_read": 45440,
                   "output": 163}),
    ]) + "\n")
    return state


def test_an_acp_session_is_priced_from_its_result_not_from_its_messages(tmp_path):
    """Trap 6. OpenCode's AssistantText carries a message_id but usage: null.
    A per-message reader takes the dedup key, adds zeros, and says nothing.
    Measured 2026-09-24: 6 of 780 sessions in this workspace are ACP."""
    repo_path = tmp_path / "repos" / "aegis"
    repo_path.mkdir(parents=True)
    state = _state_with_acp_session(tmp_path, repo_path)

    scanner = Scanner("aegis", repo_path, since=None, until=None,
                      split_dirs=SPLIT_DIRS)
    scanner.run([], state, foreign=False)

    session = next(iter(scanner.sessions.values()))
    tokens = sum(
        counter["input"] + counter["output"] + counter["cache_read"]
        for counter in session.usage.values()
    )
    assert tokens == 91 + 163 + 45440
    cost = sum(counter["cost_micro"] for counter in session.usage.values())
    assert cost > 0
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_cost_scan.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'aegis.cost.scan'`.

- [ ] **Step 3: Write `scan.py`**

```python
"""The four transcript stores, deduplicated, with one reading path per session.

Three stores are aegis's own (``sessions/``, ``backfill/``, ``claude-import/``)
and one is foreign (``~/.claude/projects``, which claude-code purges after
thirty days). They overlap, so every record is deduplicated globally.

**One path per session, never both.** A claude-code session records its token
counts on each assistant message and repeats them on the turn's ``Result``;
reading both double-counts it. An ACP session (OpenCode, Gemini) records
``usage: null`` on its messages and the real counts only on ``Result``;
reading only messages prices it at zero, takes the dedup key, and says nothing.
So: if any per-message event in a session carries a non-null usage, the session
is read by message. Otherwise it is read by Result.

Neither path derives cost from ``cost_usd``. It is absent from the two foreign
stores, and its cumulative-versus-per-turn meaning differs by harness, so
guessing wrong is a silent multiplier. Both paths price tokens through the
model registry.
"""

from __future__ import annotations

import collections
import gzip
import json
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from aegis.cost.locality import (
    NO_MODULE,
    NOISE_DIRS,
    ROOT_MODULE,
    SPLIT_DIRS,
    module_of,
    repos_mentioned,
)
from aegis.usage.aggregate import resolve_prices

# ProviderPrices carries one cache_write rate. A 1-hour write is twice the
# 5-minute one; the aegis event stream does not split them, so the share is
# taken from the claude-code rows read in the same run.
CACHE_1H_MULT = 2

_PER_MESSAGE_EVENTS = ("AssistantThinking", "AssistantText", "ToolUse")
_FAMILIES = ("opus", "sonnet", "haiku", "gemini")


@dataclass
class SessionScan:
    key: str
    source: str
    cwd: str | None = None
    first_ts: str | None = None
    last_ts: str | None = None
    n_records: int = 0
    repo_records: collections.Counter = field(default_factory=collections.Counter)
    modules: collections.Counter = field(default_factory=collections.Counter)
    usage: dict[tuple[str, str], collections.Counter] = field(default_factory=dict)
    timestamps: list[str] = field(default_factory=list)


def iso_week(ts: str) -> str:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime("%G-W%V")
    except ValueError:
        return "?"


def in_window(ts: str | None, since: str | None, until: str | None) -> bool:
    if not ts:
        return since is None and until is None
    day = ts[:10]
    if since and day < since:
        return False
    return not (until and day > until)


def family(model: str | None, default: str = "opus") -> str:
    low = (model or "").lower()
    for name in _FAMILIES:
        if name in low:
            return name
    return default


def active_hours(timestamps: list[str], cap: int = 300) -> dict[str, float]:
    """Assisted working time per ISO week: gaps between consecutive calls,
    each capped at ``cap`` seconds so an overnight pause is not counted."""
    points = []
    for raw in timestamps:
        try:
            points.append(datetime.fromisoformat(raw.replace("Z", "+00:00")))
        except ValueError:
            continue
    points.sort()
    out: collections.Counter = collections.Counter()
    for earlier, later in zip(points, points[1:]):
        gap = (later - earlier).total_seconds()
        if gap > 0:
            out[later.strftime("%G-W%V")] += min(gap, cap)
    return dict(out)


class Scanner:
    """Walks every transcript store and accumulates per-session usage and locality."""

    def __init__(
        self,
        repo: str | None,
        repo_path: Path,
        *,
        since: str | None = None,
        until: str | None = None,
        split_dirs: frozenset[str] = SPLIT_DIRS,
    ) -> None:
        self.repo = repo
        self.repo_path = Path(repo_path)
        self.multi = repo is None
        self.since = since
        self.until = until
        self.split_dirs = split_dirs
        self.sessions: dict[str, SessionScan] = {}
        self.seen: set[str] = set()
        self.first_seen: str | None = None
        self.elapsed_s: float = 0.0
        self.module_re = (
            re.compile(rf"{re.escape(repo)}(?:-wt-[A-Za-z0-9_-]+)?/([A-Za-z0-9._/-]*)")
            if repo else None
        )
        self.bare_re: re.Pattern | None = None

    def set_bare_modules(self, modules: Iterable[str]) -> None:
        """Let a record name a module by a bare relative path, not only repos/<x>/."""
        names = [m for m in modules if m and not m.startswith("(")]
        if not names:
            return
        alt = "|".join(re.escape(m) for m in sorted(names, key=len, reverse=True))
        self.bare_re = re.compile(
            r"(?:^|[\s\"'(,:=\\])((?:" + alt + r")/[A-Za-z0-9._/-]*)"
        )

    # -- accumulation ----------------------------------------------------
    def _session(self, key: str, source: str, cwd: str | None) -> SessionScan:
        scan = self.sessions.get(key)
        if scan is None:
            scan = self.sessions[key] = SessionScan(key=key, source=source, cwd=cwd)
        if cwd and not scan.cwd:
            scan.cwd = cwd
        return scan

    def _note_paths(self, scan: SessionScan, text: str) -> None:
        names = repos_mentioned(text, fold=self.repo)
        for name in names:
            scan.repo_records[name] += 1
        if not self.multi and self.repo in names and self.module_re is not None:
            modules = set()
            for match in self.module_re.finditer(text):
                sub = match.group(1)
                if sub:
                    modules.add(module_of(sub, self.split_dirs))
            if self.bare_re:
                for match in self.bare_re.finditer(text):
                    modules.add(module_of(match.group(1), self.split_dirs))
            modules -= NOISE_DIRS
            modules.discard(ROOT_MODULE)
            for module in modules or {NO_MODULE}:
                scan.modules[module] += 1
        scan.n_records += 1

    def _add(self, scan: SessionScan, ts: str, fam: str, *,
             inp: int, out: int, cc5: int, cc1: int, cache_read: int) -> None:
        if ts:
            if self.first_seen is None or ts < self.first_seen:
                self.first_seen = ts
            scan.timestamps.append(ts)
            if not scan.first_ts or ts < scan.first_ts:
                scan.first_ts = ts
            if not scan.last_ts or ts > scan.last_ts:
                scan.last_ts = ts
        bucket = scan.usage.setdefault((iso_week(ts), fam), collections.Counter())
        bucket["input"] += inp
        bucket["output"] += out
        bucket["cc5"] += cc5
        bucket["cc1"] += cc1
        bucket["cache_read"] += cache_read
        bucket["calls"] += 1
        bucket["cost_micro"] += int(round(
            _cost_of(fam, inp, out, cc5, cc1, cache_read) * 1e6
        ))

    # -- claude-code transcripts (plain or gzipped) ----------------------
    def scan_claude(self, path: Path, source: str, prefix: str) -> None:
        opener = (
            (lambda p: gzip.open(p, "rt", errors="ignore"))
            if path.suffix == ".gz"
            else (lambda p: p.open(errors="ignore"))
        )
        try:
            handle = opener(path)
        except OSError:
            return
        with handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except (ValueError, TypeError):
                    continue  # a damaged line never takes the scan down
                ts = record.get("timestamp")
                if not in_window(ts, self.since, self.until):
                    continue
                key = f"{prefix}:{record.get('sessionId') or path.stem}"
                scan = self._session(key, source, record.get("cwd"))
                self._note_paths(scan, line)
                if record.get("type") != "assistant":
                    continue
                message = record.get("message")
                if not isinstance(message, dict):
                    continue
                mid = message.get("id")
                if mid:
                    if mid in self.seen:
                        continue
                    self.seen.add(mid)
                usage = message.get("usage") or {}
                creation = usage.get("cache_creation") or {}
                cc5 = creation.get("ephemeral_5m_input_tokens", 0) or 0
                cc1 = creation.get("ephemeral_1h_input_tokens", 0) or 0
                if cc5 + cc1 == 0:
                    cc5 = usage.get("cache_creation_input_tokens", 0) or 0
                self._add(
                    scan, ts or "", family(message.get("model")),
                    inp=usage.get("input_tokens", 0) or 0,
                    out=usage.get("output_tokens", 0) or 0,
                    cc5=cc5, cc1=cc1,
                    cache_read=usage.get("cache_read_input_tokens", 0) or 0,
                )

    # -- aegis event logs ------------------------------------------------
    def scan_aegis(self, path: Path, cc1_share: float) -> None:
        """Read one aegis session log. Chooses its path per session.

        The file is read once into records so the message-or-Result decision
        can be made before anything is counted; aegis session logs are small
        enough (the largest in this workspace is a few MB) that this costs
        less than a second pass over the disk.
        """
        records = []
        try:
            with path.open(errors="ignore") as handle:
                for line in handle:
                    try:
                        records.append((json.loads(line), line))
                    except (ValueError, TypeError):
                        continue
        except OSError:
            return

        by_message = any(
            (rec.get("event") or {}).get("t") in _PER_MESSAGE_EVENTS
            and (rec.get("event") or {}).get("usage")
            for rec, _ in records
        )

        key = f"aegis:{path.stem}"
        model, cwd, scan = "opus", None, None
        result_index = 0
        for record, line in records:
            event = record.get("event") or {}
            kind = event.get("t")
            ts = record.get("aegis_ts")
            if not in_window(ts, self.since, self.until):
                continue
            if kind == "SessionMeta":
                cwd = event.get("cwd") or cwd
            if scan is None:
                scan = self._session(key, "aegis", cwd)
            elif cwd and not scan.cwd:
                scan.cwd = cwd
            self._note_paths(scan, line)
            if kind == "SystemInit":
                model = event.get("model") or model
                continue

            if by_message:
                if kind not in _PER_MESSAGE_EVENTS:
                    continue
                mid = event.get("message_id")
                if not mid or mid in self.seen:
                    continue
                self.seen.add(mid)
            else:
                if kind != "Result":
                    continue
                # No message id on this path, so the key is positional. Two
                # copies of one session (sessions/ and backfill/) share a
                # stem, so the same turn dedupes across them.
                mid = f"{path.stem}#result{result_index}"
                result_index += 1
                if mid in self.seen:
                    continue
                self.seen.add(mid)

            usage = event.get("usage") or {}
            creation = usage.get("cache_creation", 0) or 0
            cc1 = int(round(creation * cc1_share))
            self._add(
                scan, ts or "", family(event.get("model") or model),
                inp=usage.get("input", 0) or 0,
                out=usage.get("output", 0) or 0,
                cc5=creation - cc1, cc1=cc1,
                cache_read=usage.get("cache_read", 0) or 0,
            )

    # -- driver ----------------------------------------------------------
    def run(
        self,
        claude_roots: list[tuple[Path, str, str]],
        state_dir: Path | None,
        *,
        foreign: bool = True,
    ) -> float:
        started = time.monotonic()
        claude_files: list[tuple[Path, str, str]] = []
        if foreign:
            for root, source, prefix in claude_roots:
                if root.exists():
                    claude_files += [(f, source, prefix)
                                     for f in sorted(root.rglob("*.jsonl"))]
        gz_files: list[Path] = []
        aegis_files: list[Path] = []
        if state_dir:
            imported = state_dir / "claude-import"
            if imported.exists():
                gz_files = sorted(imported.glob("*.jsonl.gz"))
            for name in ("sessions", "backfill"):
                store = state_dir / name
                if store.exists():
                    aegis_files += sorted(store.glob("*.jsonl"))

        for path, source, prefix in claude_files:
            self.scan_claude(path, source, prefix)
        for path in gz_files:
            self.scan_claude(path, "aegis-import", "i")

        # Take the 1h cache share from the full-fidelity rows already read
        # rather than assuming one.
        short = long = 0
        for scan in self.sessions.values():
            for bucket in scan.usage.values():
                short += bucket["cc5"]
                long += bucket["cc1"]
        cc1_share = long / (long + short) if (long + short) else 0.0
        for path in aegis_files:
            self.scan_aegis(path, cc1_share)

        self.elapsed_s = time.monotonic() - started
        return cc1_share


def _cost_of(fam: str, inp: int, out: int, cc5: int, cc1: int, cache_read: int) -> float:
    prices = resolve_prices("claude-code", fam)
    if prices is None:
        return 0.0
    return float(
        inp * prices.input
        + out * prices.output
        + cc5 * prices.cache_write
        + cc1 * prices.cache_write * CACHE_1H_MULT
        + cache_read * prices.cache_hit
    ) / 1e6
```

- [ ] **Step 4: Run the trap-6 test and watch it pass**

Run: `uv run pytest tests/test_cost_scan.py -q`
Expected: PASS.

- [ ] **Step 5: Add the test that a claude-code session is not double counted**

```python
def test_a_claude_code_session_is_read_by_message_not_by_result(tmp_path):
    repo_path = tmp_path / "repos" / "aegis"
    repo_path.mkdir(parents=True)
    state = tmp_path / ".aegis" / "state"
    (state / "sessions").mkdir(parents=True)
    (state / "sessions" / "deep-dijkstra.jsonl").write_text("\n".join([
        _ev("2026-06-01T12:00:00.000000Z", t="SessionMeta", handle="deep-dijkstra",
            provider="claude-code", cwd=str(repo_path)),
        _ev("2026-06-01T12:00:01.000000Z", t="SystemInit", model="claude-opus-4-7"),
        _ev("2026-06-01T12:00:02.000000Z", t="AssistantText", text="hi",
            message_id="msg_one",
            usage={"input": 10, "cache_creation": 0, "cache_read": 100, "output": 20}),
        _ev("2026-06-01T12:00:03.000000Z", t="Result", duration_ms=900, is_error=False,
            cost_usd=0.02,
            usage={"input": 10, "cache_creation": 0, "cache_read": 100, "output": 20}),
    ]) + "\n")

    scanner = Scanner("aegis", repo_path, since=None, until=None,
                      split_dirs=SPLIT_DIRS)
    scanner.run([], state, foreign=False)

    session = next(iter(scanner.sessions.values()))
    tokens = sum(c["input"] + c["output"] + c["cache_read"] for c in session.usage.values())
    assert tokens == 130  # counted once, not 260
    assert sum(c["calls"] for c in session.usage.values()) == 1
```

- [ ] **Step 6: Add the deduplication test across stores**

```python
import gzip


def test_the_same_message_in_two_stores_is_counted_once(tmp_path):
    repo_path = tmp_path / "repos" / "aegis"
    repo_path.mkdir(parents=True)
    state = tmp_path / ".aegis" / "state"
    (state / "claude-import").mkdir(parents=True)
    projects = tmp_path / "projects" / "-home-apiad-Workspace"
    projects.mkdir(parents=True)

    line = json.dumps({
        "timestamp": "2026-06-01T12:00:00.000Z",
        "type": "assistant",
        "sessionId": "s1",
        "cwd": str(repo_path),
        "message": {"id": "msg_shared", "model": "claude-opus-4-7",
                    "usage": {"input_tokens": 10, "output_tokens": 20,
                              "cache_read_input_tokens": 100,
                              "cache_creation_input_tokens": 5}},
    })
    (projects / "s1.jsonl").write_text(line + "\n")
    with gzip.open(state / "claude-import" / "s1.jsonl.gz", "wt") as fh:
        fh.write(line + "\n")

    scanner = Scanner("aegis", repo_path, since=None, until=None,
                      split_dirs=SPLIT_DIRS)
    scanner.run([(projects, "claude", "c")], state, foreign=True)

    total_calls = sum(
        c["calls"] for s in scanner.sessions.values() for c in s.usage.values()
    )
    assert total_calls == 1
    assert len(scanner.seen) == 1
```

- [ ] **Step 7: Add the `--no-foreign` test**

```python
def test_no_foreign_skips_the_home_projects_store(tmp_path):
    repo_path = tmp_path / "repos" / "aegis"
    repo_path.mkdir(parents=True)
    projects = tmp_path / "projects" / "-home-apiad-Workspace"
    projects.mkdir(parents=True)
    (projects / "s1.jsonl").write_text(json.dumps({
        "timestamp": "2026-06-01T12:00:00.000Z", "type": "assistant",
        "sessionId": "s1", "cwd": str(repo_path),
        "message": {"id": "msg_x", "model": "claude-opus-4-7",
                    "usage": {"input_tokens": 10, "output_tokens": 20}},
    }) + "\n")

    scanner = Scanner("aegis", repo_path, since=None, until=None,
                      split_dirs=SPLIT_DIRS)
    scanner.run([(projects, "claude", "c")], None, foreign=False)

    assert scanner.sessions == {}
    assert scanner.first_seen is None
```

- [ ] **Step 8: Add the Review-focus test for a damaged transcript line**

```python
def test_a_truncated_json_line_does_not_stop_the_scan(tmp_path):
    repo_path = tmp_path / "repos" / "aegis"
    repo_path.mkdir(parents=True)
    projects = tmp_path / "projects" / "-home-apiad-Workspace"
    projects.mkdir(parents=True)
    good = json.dumps({
        "timestamp": "2026-06-01T12:00:00.000Z", "type": "assistant",
        "sessionId": "s1", "cwd": str(repo_path),
        "message": {"id": "msg_good", "model": "claude-opus-4-7",
                    "usage": {"input_tokens": 10, "output_tokens": 20}},
    })
    (projects / "s1.jsonl").write_text('{"timestamp": "2026-06-01T11:0\n' + good + "\n")

    scanner = Scanner("aegis", repo_path, since=None, until=None,
                      split_dirs=SPLIT_DIRS)
    scanner.run([(projects, "claude", "c")], None, foreign=True)

    assert len(scanner.seen) == 1
```

- [ ] **Step 9: Run the file and commit**

```bash
uv run pytest tests/test_cost_scan.py -q
git commit -- src/aegis/cost/scan.py tests/test_cost_scan.py \
  -m "feat(cost): transcript scanner over the four stores, one path per session"
```

---

### Task 4: Measurement and JSON

**Files:**
- Create: `src/aegis/cost/measure.py`
- Modify: `src/aegis/cost/__init__.py`
- Test: `tests/test_cost_measure.py`

**Interfaces:**
- Consumes: `Scanner`, `git_facts`, `coverage`, `session_share`, `repo_roots`, `active_hours`.
- Produces:
  - `CostOptions` dataclass: `since`, `until`, `split_dirs`, `exclude: tuple[str, ...]`, `foreign: bool`, `extra_roots: tuple[Path, ...]`, `state_dir: Path | None`, `home_projects: Path`.
  - `RepoCost` dataclass: `repo: str`, `path: str`, `since`, `until`, `generated: str`, `cost_usd: float`, `strict_usd: float`, `workspace_usd: float`, `tokens: dict[str, float]`, `calls: float`, `sessions: float`, `hours: float`, `coverage: float`, `first_seen: str | None`, `weeks`, `modules`, `families`, `sources`, `bands`, `git: GitFacts`, `elapsed_s: float`; plus `.to_dict()`.
  - `measure(repo_path: Path, options: CostOptions) -> RepoCost`
  - `sweep(directory: Path, options: CostOptions) -> list[RepoCost]`
  - `claude_roots_for(paths: Iterable[Path], home_projects: Path) -> list[tuple[Path, str, str]]`
  - `BANDS: tuple[str, ...]`

- [ ] **Step 1: Write the failing test for the attribution band**

```python
# tests/test_cost_measure.py
import json
import subprocess
from pathlib import Path

import pytest

from aegis.cost.measure import CostOptions, measure


def _ev(ts: str, **event) -> str:
    return json.dumps({"v": 1, "aegis_ts": ts, "event": event})


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A workspace with two repos and three aegis sessions: one inside the
    target repo, one mixed, one entirely elsewhere."""
    for name in ("aegis", "une-tools"):
        repo = tmp_path / "repos" / name
        repo.mkdir(parents=True)
        subprocess.run(("git", "init", "-q", "-b", "main"), cwd=repo, check=True,
                       capture_output=True)
        for key, value in (("user.email", "t@example.com"), ("user.name", "Tester")):
            subprocess.run(("git", "config", key, value), cwd=repo, check=True,
                           capture_output=True)
        (repo / "src").mkdir()
        (repo / "src" / "main.py").write_text("a = 1\n")
        subprocess.run(("git", "add", "--", "src/main.py"), cwd=repo, check=True,
                       capture_output=True)
        subprocess.run(("git", "commit", "-q", "-m", "feat: start"), cwd=repo,
                       check=True, capture_output=True,
                       env={"PATH": "/usr/bin:/bin", "HOME": str(repo),
                            "GIT_AUTHOR_DATE": "2026-06-01T12:00:00+00:00",
                            "GIT_COMMITTER_DATE": "2026-06-01T12:00:00+00:00",
                            "GIT_AUTHOR_NAME": "Tester",
                            "GIT_AUTHOR_EMAIL": "t@example.com",
                            "GIT_COMMITTER_NAME": "Tester",
                            "GIT_COMMITTER_EMAIL": "t@example.com"})

    sessions = tmp_path / ".aegis" / "state" / "sessions"
    sessions.mkdir(parents=True)

    def usage(n):
        return {"input": n, "cache_creation": 0, "cache_read": n * 10, "output": n}

    # inside the target repo: share 1.0
    (sessions / "inside.jsonl").write_text("\n".join([
        _ev("2026-06-02T10:00:00.000000Z", t="SessionMeta", handle="inside",
            provider="claude-code", cwd=str(tmp_path / "repos" / "aegis")),
        _ev("2026-06-02T10:00:01.000000Z", t="SystemInit", model="claude-opus-4-7"),
        _ev("2026-06-02T10:00:02.000000Z", t="AssistantText", text="x",
            message_id="m1", usage=usage(1000)),
    ]) + "\n")
    # mixed: three records name aegis, one names une-tools → share 0.75
    (sessions / "mixed.jsonl").write_text("\n".join([
        _ev("2026-06-02T11:00:00.000000Z", t="SessionMeta", handle="mixed",
            provider="claude-code", cwd=str(tmp_path)),
        _ev("2026-06-02T11:00:01.000000Z", t="SystemInit", model="claude-opus-4-7"),
        _ev("2026-06-02T11:00:02.000000Z", t="AssistantText",
            text="repos/aegis/src/a.py repos/aegis/src/b.py", message_id="m2",
            usage=usage(1000)),
        _ev("2026-06-02T11:00:03.000000Z", t="AssistantText",
            text="repos/aegis/src/c.py", message_id="m3", usage=usage(0)),
        _ev("2026-06-02T11:00:04.000000Z", t="AssistantText",
            text="repos/une-tools/app.py", message_id="m4", usage=usage(0)),
    ]) + "\n")
    # elsewhere: share 0
    (sessions / "elsewhere.jsonl").write_text("\n".join([
        _ev("2026-06-02T12:00:00.000000Z", t="SessionMeta", handle="elsewhere",
            provider="claude-code", cwd=str(tmp_path)),
        _ev("2026-06-02T12:00:01.000000Z", t="SystemInit", model="claude-opus-4-7"),
        _ev("2026-06-02T12:00:02.000000Z", t="AssistantText",
            text="repos/une-tools/app.py", message_id="m5", usage=usage(1000)),
    ]) + "\n")
    return tmp_path


def _options(tree: Path) -> CostOptions:
    return CostOptions(state_dir=tree / ".aegis" / "state", foreign=False,
                       home_projects=tree / "no-such-projects")


def test_proportional_and_strict_attribution_bracket_the_answer(tree):
    result = measure(tree / "repos" / "aegis", _options(tree))

    # inside contributes whole; mixed contributes 3/4 of its own cost.
    assert result.strict_usd < result.cost_usd
    assert result.cost_usd < result.workspace_usd
    assert result.bands["0 (no mention)"]["sessions"] == 1
    assert result.bands["1.0 (this repo only)"]["sessions"] == 1
    assert result.bands["0.1-0.8 (mixed)"]["sessions"] == 1
    assert result.bands["0.1-0.8 (mixed)"]["attributed"] == pytest.approx(
        result.bands["0.1-0.8 (mixed)"]["cost"] * 0.75, rel=1e-6
    )
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_cost_measure.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'aegis.cost.measure'`.

- [ ] **Step 3: Write `measure.py`**

```python
"""Assembling one repo's answer: cost, volume, coverage, and the error bar.

Two attribution rules run on every measurement and both are reported.
Proportional gives every session its share; strict counts only sessions above
0.8 and counts them whole. The band between them is the error bar: 3.2% on
une-tools and 7.5% on aegis. A report that hides it invites the question it
cannot answer.
"""

from __future__ import annotations

import collections
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from aegis.cost.gitfacts import GitFacts, coverage, git_facts
from aegis.cost.locality import SPLIT_DIRS, WHOLE_REPO, NO_MODULE, repo_roots, session_share
from aegis.cost.scan import Scanner, active_hours

STRICT_FLOOR = 0.8

BANDS = (
    "0 (no mention)",
    "<0.1 (passing mention)",
    "0.1-0.8 (mixed)",
    "0.8-0.95 (almost only this repo)",
    "1.0 (this repo only)",
)


def _band(share: float) -> str:
    if share == 0:
        return BANDS[0]
    if share < 0.1:
        return BANDS[1]
    if share < 0.8:
        return BANDS[2]
    if share < 0.95:
        return BANDS[3]
    return BANDS[4]


@dataclass
class CostOptions:
    since: str | None = None
    until: str | None = None
    split_dirs: frozenset[str] = SPLIT_DIRS
    exclude: tuple[str, ...] = ()
    foreign: bool = True
    extra_roots: tuple[Path, ...] = ()
    state_dir: Path | None = None
    home_projects: Path = field(
        default_factory=lambda: Path.home() / ".claude" / "projects"
    )


@dataclass
class RepoCost:
    repo: str
    path: str
    since: str | None
    until: str | None
    generated: str
    cost_usd: float
    strict_usd: float
    workspace_usd: float
    tokens: dict[str, float]
    calls: float
    sessions: float
    hours: float
    coverage: float
    first_seen: str | None
    weeks: dict[str, dict[str, float]]
    modules: dict[str, float]
    families: dict[str, float]
    sources: dict[str, float]
    bands: dict[str, dict[str, float]]
    git: GitFacts
    elapsed_s: float

    def to_dict(self) -> dict:
        data = asdict(self)
        data["git"] = asdict(self.git)
        data["git"].pop("dates")  # one line per commit, and nothing reads it back
        return data


def claude_roots_for(
    paths: Iterable[Path], home_projects: Path
) -> list[tuple[Path, str, str]]:
    """The ``~/.claude/projects`` directories that belong to these paths.

    Claude Code names a project directory after its cwd with "/" turned into
    "-", so a path prefix selects that tree and everything under it, and
    leaves /tmp and pytest scratch directories out.
    """
    if not home_projects.exists():
        return []
    prefixes = {str(Path(p).resolve()).replace("/", "-") for p in paths}
    roots: list[tuple[Path, str, str]] = []
    for entry in sorted(home_projects.iterdir()):
        if not entry.is_dir():
            continue
        if any(entry.name == p or entry.name.startswith(p + "-") for p in prefixes):
            roots.append((entry, "claude", "c"))
    return roots


def measure(repo_path: Path, options: CostOptions) -> RepoCost:
    repo_path = Path(repo_path).resolve()
    name = repo_path.name
    facts = git_facts(repo_path, since=options.since, until=options.until,
                      split_dirs=options.split_dirs, exclude=options.exclude)

    # Two grandparents up is the workspace under the repos/<name> layout this
    # was written for. It only widens which ~/.claude/projects directories are
    # read, so a repo laid out differently loses transcripts rather than
    # gaining wrong ones, and --extra-root puts them back.
    workspace = repo_path.parent.parent
    scanner = Scanner(name, repo_path, since=options.since, until=options.until,
                      split_dirs=options.split_dirs)
    scanner.set_bare_modules(sorted(facts.loc))
    roots = claude_roots_for([workspace, repo_path], options.home_projects)
    roots += [(Path(p), "extra", "x") for p in options.extra_roots]
    scanner.run(roots, options.state_dir, foreign=options.foreign)

    roots_for_repo = repo_roots(repo_path)
    weeks: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    modules: collections.Counter = collections.Counter()
    families: collections.Counter = collections.Counter()
    sources: collections.Counter = collections.Counter()
    totals: collections.Counter = collections.Counter()
    bands: dict[str, collections.Counter] = {b: collections.Counter() for b in BANDS}
    n_sessions = strict_usd = strict_calls = workspace_usd = 0.0
    hours = 0.0

    for scan in scanner.sessions.values():
        share = session_share(name, scan.repo_records, scan.cwd, roots_for_repo)
        own_cost = sum(b["cost_micro"] for b in scan.usage.values()) / 1e6
        workspace_usd += own_cost
        band = bands[_band(share)]
        band["sessions"] += 1
        band["cost"] += own_cost
        band["attributed"] += own_cost * share
        if share >= STRICT_FLOOR:
            strict_usd += own_cost
            strict_calls += sum(b["calls"] for b in scan.usage.values())
        if share <= 0:
            continue

        n_sessions += share
        for week, seconds in active_hours(scan.timestamps).items():
            weeks[week]["active_s"] += seconds * share
            hours += seconds * share / 3600
        mine_cost = 0.0
        for (week, fam), bucket in scan.usage.items():
            cost = bucket["cost_micro"] / 1e6 * share
            tokens = (bucket["input"] + bucket["output"] + bucket["cc5"]
                      + bucket["cc1"] + bucket["cache_read"]) * share
            weeks[week]["cost"] += cost
            weeks[week]["tokens"] += tokens
            weeks[week]["calls"] += bucket["calls"] * share
            families[fam] += cost
            for key in ("input", "output", "cc5", "cc1", "cache_read"):
                totals[key] += bucket[key] * share
            totals["cost"] += cost
            totals["tokens"] += tokens
            totals["calls"] += bucket["calls"] * share
            mine_cost += cost
        sources[scan.source] += mine_cost

        named = dict(scan.modules)
        loose = named.pop(NO_MODULE, 0)
        total_named = sum(named.values())
        if total_named:
            # Repo-wide records follow the session's own module mix.
            for module, count in named.items():
                modules[module] += mine_cost * count / total_named
        elif loose:
            modules[WHOLE_REPO] += mine_cost

    return RepoCost(
        repo=name,
        path=str(repo_path),
        since=options.since,
        until=options.until,
        generated=datetime.now(timezone.utc).isoformat(),
        cost_usd=totals["cost"],
        strict_usd=strict_usd,
        workspace_usd=workspace_usd,
        tokens={k: totals[k] for k in ("input", "output", "cc5", "cc1",
                                       "cache_read", "tokens")},
        calls=totals["calls"],
        sessions=n_sessions,
        hours=hours,
        coverage=coverage(facts.dates, scanner.first_seen),
        first_seen=scanner.first_seen,
        weeks={k: dict(v) for k, v in sorted(weeks.items())},
        modules=dict(modules.most_common()),
        families=dict(families),
        sources=dict(sources),
        bands={k: dict(v) for k, v in bands.items()},
        git=facts,
        elapsed_s=scanner.elapsed_s,
    )


def sweep(directory: Path, options: CostOptions) -> list[RepoCost]:
    """Every git repo directly under ``directory``, one scan for all of them.

    The per-record repo tally already covers every repo, so the scan runs once
    and each repo's share is read off it. Doing it per repo would re-read
    3,672 files eighty times.
    """
    directory = Path(directory).resolve()
    repos = [d for d in sorted(directory.iterdir()) if (d / ".git").exists()]
    scanner = Scanner(None, directory, since=options.since, until=options.until,
                      split_dirs=options.split_dirs)
    roots = claude_roots_for([directory.parent, directory], options.home_projects)
    roots += [(Path(p), "extra", "x") for p in options.extra_roots]
    scanner.run(roots, options.state_dir, foreign=options.foreign)

    precomputed = [
        (scan,
         sum(b["cost_micro"] for b in scan.usage.values()) / 1e6,
         sum(b["calls"] for b in scan.usage.values()),
         sum(active_hours(scan.timestamps).values()) / 3600)
        for scan in scanner.sessions.values()
    ]

    out: list[RepoCost] = []
    for repo_path in repos:
        try:
            facts = git_facts(repo_path, since=options.since, until=options.until,
                              split_dirs=options.split_dirs, exclude=options.exclude)
        except (RuntimeError, OSError):
            continue  # a broken repo must not stop the sweep
        roots_for_repo = repo_roots(repo_path)
        cost = calls = hours = 0.0
        for scan, own_cost, own_calls, own_hours in precomputed:
            share = session_share(repo_path.name, scan.repo_records, scan.cwd,
                                  roots_for_repo)
            if share <= 0:
                continue
            cost += own_cost * share
            calls += own_calls * share
            hours += own_hours * share
        out.append(RepoCost(
            repo=repo_path.name, path=str(repo_path), since=options.since,
            until=options.until,
            generated=datetime.now(timezone.utc).isoformat(),
            cost_usd=cost, strict_usd=0.0, workspace_usd=0.0,
            tokens={}, calls=calls, sessions=0.0, hours=hours,
            coverage=coverage(facts.dates, scanner.first_seen),
            first_seen=scanner.first_seen, weeks={}, modules={}, families={},
            sources={}, bands={}, git=facts, elapsed_s=scanner.elapsed_s,
        ))
    out.sort(key=lambda r: -r.cost_usd)
    return out
```

Then extend `src/aegis/cost/__init__.py`:

```python
from aegis.cost.measure import BANDS, CostOptions, RepoCost, measure, sweep

__all__ = ["BANDS", "CostOptions", "RepoCost", "measure", "sweep"]
```

- [ ] **Step 4: Run the band test and watch it pass**

Run: `uv run pytest tests/test_cost_measure.py -q`
Expected: PASS.

- [ ] **Step 5: Add the JSON-shape test**

```python
def test_to_dict_is_json_serialisable_and_drops_the_date_list(tree):
    result = measure(tree / "repos" / "aegis", _options(tree))
    data = result.to_dict()

    text = json.dumps(data)  # raises if anything is not serialisable
    assert "dates" not in data["git"]
    assert data["repo"] == "aegis"
    assert data["coverage"] == 1.0
    assert set(data["bands"]) == set(
        ["0 (no mention)", "<0.1 (passing mention)", "0.1-0.8 (mixed)",
         "0.8-0.95 (almost only this repo)", "1.0 (this repo only)"]
    )
    assert json.loads(text)["path"].endswith("/repos/aegis")
```

- [ ] **Step 6: Add the Review-focus test for a repo with no commits in the window**

```python
def test_a_repo_with_no_commits_in_the_window_measures_without_raising(tree):
    options = _options(tree)
    options.since = "2027-01-01"

    result = measure(tree / "repos" / "aegis", options)

    assert result.git.n_commits == 0
    assert result.cost_usd == 0.0
    assert result.coverage == 1.0
    assert json.dumps(result.to_dict())
```

- [ ] **Step 7: Run the file and commit**

```bash
uv run pytest tests/test_cost_measure.py -q
git commit -- src/aegis/cost/measure.py src/aegis/cost/__init__.py tests/test_cost_measure.py \
  -m "feat(cost): assemble a repo measurement with both attribution rules"
```

---

### Task 5: The two subcommands

**Files:**
- Create: `src/aegis/cost/render.py`
- Modify: `src/aegis/cli_usage.py`
- Test: `tests/test_cost_cli.py`

**Interfaces:**
- Consumes: `measure`, `sweep`, `CostOptions`, `RepoCost`, `BANDS`.
- Produces:
  - `aegis.cost.render.repo_lines(result: RepoCost) -> list[str]`
  - `aegis.cost.render.sweep_lines(rows: list[RepoCost]) -> list[str]`
  - `aegis.cost.measure.cache_path(state_dir: Path, repo: str) -> Path`
  - `aegis.cost.measure.write_cache(state_dir: Path, result: RepoCost) -> Path`
  - `aegis.cost.measure.read_cache(state_dir: Path, repo: str) -> dict | None`
  - typer commands `repo` and `repos` on the existing `aegis usage` app.

- [ ] **Step 1: Write the failing CLI test**

```python
# tests/test_cost_cli.py
import json
from pathlib import Path

from typer.testing import CliRunner

from aegis.cli_usage import app

runner = CliRunner()


def test_usage_repo_prints_a_table_with_the_error_bar(tree, monkeypatch):
    monkeypatch.chdir(tree)
    result = runner.invoke(app, ["repo", str(tree / "repos" / "aegis"),
                                 "--no-foreign", "--state", str(tree / ".aegis" / "state")])

    assert result.exit_code == 0, result.output
    assert "aegis" in result.output
    assert "proportional" in result.output.lower()
    assert "strict" in result.output.lower()
    assert "coverage" in result.output.lower()
```

(The `tree` fixture is the one from Task 4; move it to `tests/conftest.py` as
`cost_tree` if the two files need it, and import it by that name in both.)

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_cost_cli.py -q`
Expected: FAIL, `No such command 'repo'`.

- [ ] **Step 3: Write `render.py`**

```python
"""English terminal tables for a repo measurement.

Plain text on purpose, matching ``aegis.usage.render``: one column of labels
and one of numbers, so a terminal, a pipe and a test all read the same thing.
Charts and narrative live outside aegis.
"""

from __future__ import annotations

from aegis.cost.measure import RepoCost


def _usd(value: float) -> str:
    return f"{value:,.2f}"


def _num(value: float) -> str:
    return f"{value:,.0f}"


def repo_lines(result: RepoCost) -> list[str]:
    git = result.git
    code = sum(w.get("code", 0) for w in git.weeks.values())
    loc_code = sum(v.get("code", 0) for v in git.loc.values())
    lines = [
        f"{result.repo}  {result.path}",
        f"window {result.since or (git.first or '?')[:10]} .. "
        f"{result.until or (git.last or '?')[:10]}   "
        f"scanned in {result.elapsed_s:.0f}s",
        "",
        f"  cost (proportional)   {_usd(result.cost_usd)} USD",
        f"  cost (strict >= 0.8)  {_usd(result.strict_usd)} USD",
        f"  workspace total       {_usd(result.workspace_usd)} USD",
        f"  tokens                {_num(result.tokens.get('tokens', 0) / 1e6)} M",
        f"  model calls           {_num(result.calls)}",
        f"  sessions              {result.sessions:.1f}",
        f"  assisted hours        {result.hours:.0f}",
        f"  commits               {_num(git.n_commits)}",
        f"  code lines in tree    {_num(loc_code)}",
        f"  coverage              {100 * result.coverage:.0f}%"
        + ("   PARTIAL: commits predate the oldest transcript "
           f"({(result.first_seen or '?')[:10]}); pass --since to compare"
           if result.coverage < 0.98 else ""),
        "",
    ]
    if result.cost_usd > 0:
        lines += [
            "unit cost",
            f"  per commit            {_usd(result.cost_usd / max(git.n_commits, 1))}",
            f"  per 1k code lines     {_usd(result.cost_usd / max(code, 1) * 1000)}",
            f"  per assisted hour     {_usd(result.cost_usd / max(result.hours, 1e-9))}",
            "",
        ]
    if result.modules:
        lines.append("cost by module")
        for module, value in list(result.modules.items())[:14]:
            lines.append(f"  {module:<28} {_usd(value):>12}")
        lines.append("")
    lines.append("attribution bands (the error bar)")
    for band, row in result.bands.items():
        lines.append(
            f"  {band:<34} {int(row.get('sessions', 0)):>4} sessions"
            f"  own {_usd(row.get('cost', 0)):>12}"
            f"  attributed {_usd(row.get('attributed', 0)):>12}"
        )
    if git.excluded:
        lines += ["", "excluded from the git side: " + ", ".join(git.excluded)]
    lines += [
        "",
        "sources: " + (", ".join(f"{k} {_usd(v)}" for k, v in result.sources.items())
                       or "none"),
        "Dollars are API list-price equivalents, not an invoice.",
    ]
    return lines


def sweep_lines(rows: list[RepoCost]) -> list[str]:
    lines = [
        f"{'repo':<26}{'cost USD':>12}{'commits':>10}{'code':>10}"
        f"{'hours':>8}{'coverage':>10}"
    ]
    for row in rows:
        loc_code = sum(v.get("code", 0) for v in row.git.loc.values())
        lines.append(
            f"{row.repo:<26}{_usd(row.cost_usd):>12}{_num(row.git.n_commits):>10}"
            f"{_num(loc_code):>10}{row.hours:>8.0f}{100 * row.coverage:>9.0f}%"
        )
    total = sum(r.cost_usd for r in rows)
    lines += ["", f"total {_usd(total)} USD across {len(rows)} repos"]
    thin = [r for r in rows if r.coverage < 0.8 and r.git.n_commits > 20]
    if thin:
        floor = (rows[0].first_seen or "?")[:10] if rows else "?"
        lines.append(
            f"coverage below 80% in {len(thin)} repos: their commits predate the "
            f"oldest surviving transcript ({floor}), so they look falsely cheap. "
            f"Re-run with --since {floor} to compare them: "
            + ", ".join(f"{r.repo} {100 * r.coverage:.0f}%" for r in thin[:12])
        )
    return lines
```

- [ ] **Step 4: Add the cache helpers to `measure.py`**

```python
def cache_path(state_dir: Path, repo: str) -> Path:
    return Path(state_dir) / "cost" / f"{repo}.json"


def write_cache(state_dir: Path, result: RepoCost) -> Path:
    path = cache_path(state_dir, result.repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result.to_dict(), indent=1, default=str))
    return path


def read_cache(state_dir: Path, repo: str) -> dict | None:
    path = cache_path(state_dir, repo)
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None
```

with `import json` added at the top of `measure.py`.

- [ ] **Step 5: Add the two subcommands to `cli_usage.py`**

```python
# added imports
from aegis.cost import CostOptions, measure, sweep
from aegis.cost.locality import SPLIT_DIRS
from aegis.cost.measure import write_cache
from aegis.cost.render import repo_lines, sweep_lines


def _cost_options(
    since, until, no_foreign, exclude, split, extra_root, state
) -> CostOptions:
    root = find_project_root() or Path.cwd()
    return CostOptions(
        since=since,
        until=until,
        split_dirs=frozenset(s for s in (split or "").split(",") if s) or SPLIT_DIRS,
        exclude=tuple(exclude or ()),
        foreign=not no_foreign,
        extra_roots=tuple(Path(p) for p in (extra_root or ())),
        state_dir=Path(state) if state else state_dir(root),
    )


@app.command("repo")
def repo_cost(
    path: str = typer.Argument(..., help="path to the git repo to measure"),
    since: str = typer.Option(None, "--since", help="ISO date lower bound"),
    until: str = typer.Option(None, "--until", help="ISO date upper bound"),
    no_foreign: bool = typer.Option(
        False, "--no-foreign", help="skip ~/.claude/projects"
    ),
    exclude: list[str] = typer.Option(
        None, "--exclude", help="glob dropped from the git side (repeatable)"
    ),
    split: str = typer.Option(
        None, "--split", help="comma-separated monorepo containers"
    ),
    extra_root: list[str] = typer.Option(
        None, "--extra-root", help="another directory of claude transcripts"
    ),
    state: str = typer.Option(None, "--state", help="override the aegis state dir"),
    as_json: bool = typer.Option(False, "--json", help="print the full structure"),
) -> None:
    """What one repository cost to build, measured against its transcripts."""
    options = _cost_options(since, until, no_foreign, exclude, split, extra_root, state)
    target = Path(path)
    if not (target / ".git").exists():
        typer.echo(f"not a git repo: {target}")
        raise typer.Exit(2)
    result = measure(target, options)
    if options.state_dir:
        write_cache(options.state_dir, result)
    if as_json:
        typer.echo(json.dumps(result.to_dict(), indent=1, default=str))
    else:
        typer.echo("\n".join(repo_lines(result)))


@app.command("repos")
def repos_cost(
    directory: str = typer.Argument(..., help="directory of git repos to sweep"),
    since: str = typer.Option(None, "--since", help="ISO date lower bound"),
    until: str = typer.Option(None, "--until", help="ISO date upper bound"),
    no_foreign: bool = typer.Option(
        False, "--no-foreign", help="skip ~/.claude/projects"
    ),
    exclude: list[str] = typer.Option(None, "--exclude", help="glob dropped from git"),
    split: str = typer.Option(None, "--split", help="monorepo containers"),
    extra_root: list[str] = typer.Option(None, "--extra-root", help="extra transcripts"),
    state: str = typer.Option(None, "--state", help="override the aegis state dir"),
    as_json: bool = typer.Option(False, "--json", help="print the full structure"),
) -> None:
    """Every git repo directly under a directory, one transcript sweep."""
    options = _cost_options(since, until, no_foreign, exclude, split, extra_root, state)
    target = Path(directory)
    if not target.is_dir():
        typer.echo(f"not a directory: {target}")
        raise typer.Exit(2)
    rows = sweep(target, options)
    if as_json:
        typer.echo(json.dumps([r.to_dict() for r in rows], indent=1, default=str))
    else:
        typer.echo("\n".join(sweep_lines(rows)))
```

with `import json` added at the top of `cli_usage.py`.

- [ ] **Step 6: Run the CLI test and watch it pass**

Run: `uv run pytest tests/test_cost_cli.py -q`
Expected: PASS.

- [ ] **Step 7: Add the `--json` and cache tests**

```python
def test_usage_repo_json_writes_the_cache_under_state(tree, monkeypatch):
    monkeypatch.chdir(tree)
    state = tree / ".aegis" / "state"
    result = runner.invoke(app, ["repo", str(tree / "repos" / "aegis"),
                                 "--no-foreign", "--state", str(state), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["repo"] == "aegis"
    cached = json.loads((state / "cost" / "aegis.json").read_text())
    assert cached["repo"] == "aegis"


def test_usage_repo_refuses_a_path_that_is_not_a_git_repo(tmp_path):
    result = runner.invoke(app, ["repo", str(tmp_path)])
    assert result.exit_code == 2
    assert "not a git repo" in result.output
```

- [ ] **Step 8: Add the Review-focus test for a non-git entry in a sweep**

```python
def test_a_sweep_skips_a_non_git_directory_and_keeps_going(tree, monkeypatch):
    (tree / "repos" / "scratch").mkdir()
    monkeypatch.chdir(tree)

    result = runner.invoke(app, ["repos", str(tree / "repos"), "--no-foreign",
                                 "--state", str(tree / ".aegis" / "state")])

    assert result.exit_code == 0, result.output
    assert "aegis" in result.output
    assert "une-tools" in result.output
    assert "scratch" not in result.output
    assert "across 2 repos" in result.output
```

- [ ] **Step 9: Run the file and commit**

```bash
uv run pytest tests/test_cost_cli.py -q
git commit -- src/aegis/cost/render.py src/aegis/cost/measure.py src/aegis/cli_usage.py tests/test_cost_cli.py \
  -m "feat(cost): aegis usage repo and aegis usage repos"
```

---

### Task 6: The MCP tool

**Files:**
- Modify: `src/aegis/mcp/server.py`
- Test: `tests/test_cost_mcp.py`

**Interfaces:**
- Consumes: `aegis.cost.measure.{read_cache, write_cache, measure, CostOptions}`.
- Produces: MCP tool `aegis_repo_cost(repo: str, from_handle: str, refresh: bool = False) -> dict`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cost_mcp.py
import json
from pathlib import Path

from aegis.cost.measure import cache_path


def test_repo_cost_tool_reads_the_cache_and_reports_its_age(tmp_path):
    from aegis.mcp.server import repo_cost_payload  # pure helper, no server needed

    state = tmp_path / "state"
    path = cache_path(state, "aegis")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "repo": "aegis", "cost_usd": 123.45, "strict_usd": 120.0,
        "coverage": 0.93, "hours": 40.0,
        "generated": "2026-09-24T06:00:00+00:00",
        "modules": {"src": 100.0}, "git": {"n_commits": 300},
    }))

    payload = repo_cost_payload(state, "aegis", now="2026-09-24T12:00:00+00:00")

    assert payload["repo"] == "aegis"
    assert payload["cost_usd"] == 123.45
    assert payload["cache_age_hours"] == 6.0


def test_repo_cost_tool_says_where_it_looked_when_there_is_no_cache(tmp_path):
    from aegis.mcp.server import repo_cost_payload

    payload = repo_cost_payload(tmp_path / "state", "aegis", now="2026-09-24T12:00:00+00:00")

    assert "error" in payload
    assert "aegis.json" in payload["error"]
    assert "refresh" in payload["error"]
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_cost_mcp.py -q`
Expected: FAIL, `ImportError: cannot import name 'repo_cost_payload'`.

- [ ] **Step 3: Add the helper and the tool to `mcp/server.py`**

Near the other module-level helpers:

```python
def repo_cost_payload(state_dir: Path, repo: str, *, now: str | None = None) -> dict:
    """The cached repo-cost summary plus how old it is, or an error naming
    the file it looked for.

    A full sweep reads 3,672 transcript files in 60 seconds of CPU, which is
    too long for a call that blocks an agent's turn. The tool serves the last
    computed answer and says how stale it is; ``refresh=True`` pays the minute.
    """
    from datetime import datetime, timezone

    from aegis.cost.measure import cache_path, read_cache

    data = read_cache(state_dir, repo)
    if data is None:
        return {
            "error": f"no cached measurement at {cache_path(state_dir, repo)}; "
            f"run `aegis usage repo <path>` or call this tool with refresh=true"
        }
    at = datetime.now(timezone.utc) if now is None else datetime.fromisoformat(now)
    try:
        generated = datetime.fromisoformat(data["generated"])
    except (KeyError, ValueError):
        age = None
    else:
        age = round((at - generated).total_seconds() / 3600, 2)
    return {
        "repo": data.get("repo", repo),
        "cost_usd": data.get("cost_usd"),
        "strict_usd": data.get("strict_usd"),
        "coverage": data.get("coverage"),
        "hours": data.get("hours"),
        "commits": (data.get("git") or {}).get("n_commits"),
        "modules": dict(list((data.get("modules") or {}).items())[:10]),
        "generated": data.get("generated"),
        "cache_age_hours": age,
        "note": "API list-price equivalents, not an invoice",
    }
```

and inside `build_server`, beside `aegis_budget_status`:

```python
    @server.tool
    async def aegis_repo_cost(
        repo: str, from_handle: str, refresh: bool = False
    ) -> dict:
        """What a repository cost to build: tokens at list price, attributed
        to that repo by locality, plus commits and transcript coverage.

        Serves the last computed answer with its age in hours, because a full
        recomputation reads thousands of transcript files and takes about a
        minute. refresh=True recomputes and pays that minute; pass it only
        when this morning's figure will not do.
        """
        state_dir = bridge.state_root
        if refresh:
            import asyncio

            from aegis.cost import CostOptions, measure
            from aegis.cost.measure import write_cache

            target = Path(repo)
            if not (target / ".git").exists():
                return {"error": f"not a git repo: {target}"}
            options = CostOptions(state_dir=state_dir)
            result = await asyncio.to_thread(measure, target, options)
            write_cache(state_dir, result)
            return repo_cost_payload(state_dir, result.repo)
        return repo_cost_payload(state_dir, Path(repo).name)
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `uv run pytest tests/test_cost_mcp.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git commit -- src/aegis/mcp/server.py tests/test_cost_mcp.py \
  -m "feat(mcp): aegis_repo_cost reads the cached repo measurement"
```

---

### Task 7: Documentation, changelog, and the wrapper

**Files:**
- Modify: `docs/usage.md`, `CHANGELOG.md`, `docs/mcp.md`
- Modify (Workspace repo, separate commit): `/home/apiad/Workspace/bin/dev-cost-report`, `/home/apiad/Workspace/.claude/commands/cost-report.md`, `/home/apiad/Workspace/vault/Atlas/Know-how/measuring-what-a-repo-cost-to-build.md`

- [ ] **Step 1: Add the section to `docs/usage.md`**

Directly after the `aegis usage` section, a `### What a repo cost to build` heading covering: the two commands with their options, the locality method in three sentences, the two attribution rules and the band between them, the coverage figure and what a partial one means, and one paragraph named "Why this does not match `aegis usage`" stating that `aegis usage` reports claude-code's billed `cost_usd` over `<state>/sessions/` alone while `aegis usage repo` reports token math at list price over four stores.

- [ ] **Step 2: Add the tool to `docs/mcp.md`**

One row or paragraph for `aegis_repo_cost`, naming the cache path `<state>/cost/<repo>.json` and the `refresh` argument.

- [ ] **Step 3: Add the CHANGELOG entry**

Under the unreleased heading:

```markdown
- `aegis usage repo <path>` and `aegis usage repos <dir>` measure what a
  repository cost to build: tokens priced against the model registry across
  all four transcript stores, attributed to the repo by locality, joined with
  classified git history and a transcript-coverage figure. `aegis_repo_cost`
  serves the cached answer to agents.
```

- [ ] **Step 4: Run the doc linter**

Run: `make lint-docs`
Expected: exit 0. The CLI-roster rule only extracts from `src/aegis/cli.py`, so
it will not catch these subcommands; the file-path rule will check every path
named in the new prose.

- [ ] **Step 5: Run the whole gate**

Run: `make check`
Expected: all five stages pass.

- [ ] **Step 6: Exercise it the way a user reaches it**

```bash
uv run aegis usage repo /home/apiad/Workspace/repos/aegis --since 2026-05-29
uv run aegis usage repos /home/apiad/Workspace/repos --since 2026-05-29 | head -20
aegis kill --all && aegis serve &   # the daemon must boot after the change
```

Then, from an agent session attached to that daemon, call `aegis_repo_cost`
with `repo="aegis"` and confirm it returns the cache written by the first
command. Compare the `aegis usage repo` total against
`bin/dev-cost-report aegis --json-only` from before the change: the spec's
verification rule is that a difference you can name means it works, and an
exact match to four digits proves nothing.

- [ ] **Step 7: Commit the aegis side**

```bash
git commit -- docs/usage.md docs/mcp.md CHANGELOG.md \
  -m "docs: aegis usage repo, aegis usage repos, and aegis_repo_cost"
```

- [ ] **Step 8: Rewrite `bin/dev-cost-report` as a narrator**

In `/home/apiad/Workspace`, replace the engine half of the script with a call
to `aegis usage repo <path> --json` (and `aegis usage repos <dir> --json` for
`--all`), keeping `make_charts`, `build_report`, `github_facts` and `main`.
The JSON keys change from the old `att`/`git` nesting to `RepoCost.to_dict()`,
so the report builder reads `data["cost_usd"]`, `data["bands"]`,
`data["modules"]`, `data["weeks"]` and `data["git"]["weeks"]`. Keep the header
comment, update it to say the arithmetic now lives in aegis and this file is
the narrator.

Verify against the committed report:

```bash
bin/dev-cost-report une-tools --since 2026-05-29 --out /tmp/une-check
diff <(head -40 /tmp/une-check/report.md) <(head -40 repos/une-tools/docs/2026-09-24-informe-costo-desarrollo.md) || true
```

The figures will differ, because pricing moved to the model registry and the
ACP sessions now count. Name the difference in the commit message rather than
tuning until it matches.

- [ ] **Step 9: Update the command and the know-how**

`.claude/commands/cost-report.md`: the invocation becomes
`aegis usage repo <path>` for raw numbers and `bin/dev-cost-report` for the
narrative. The trap list stays, with the self-counting trap now naming
`--exclude` instead of asking the writer to remember.

`vault/Atlas/Know-how/measuring-what-a-repo-cost-to-build.md`: add the sixth
trap (an ACP session priced at zero, 6 of 780 sessions on this workspace as of
2026-09-24), and point the opening line at `aegis usage repo`.

- [ ] **Step 10: Commit the Workspace side**

```bash
cd /home/apiad/Workspace
git commit -- bin/dev-cost-report .claude/commands/cost-report.md \
  vault/Atlas/Know-how/measuring-what-a-repo-cost-to-build.md \
  -m "refactor(cost): dev-cost-report narrates, aegis usage repo measures"
```
