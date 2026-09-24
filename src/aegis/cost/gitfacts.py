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
        repo_path,
        "log",
        "--all",
        "--no-merges",
        "--date=iso-strict",
        f"--format=%H{SEP}%ad{SEP}%an{SEP}%s",
        "--numstat",
        *window,
    )

    commits: list[dict] = []
    current: dict | None = None
    # NB: split on "\n", never splitlines(). See the module docstring.
    for line in raw.split("\n"):
        if SEP in line:
            if current:
                commits.append(current)
            sha, date, author, subject = line.split(SEP, 3)
            current = {
                "sha": sha,
                "date": date,
                "author": author,
                "subject": subject,
                "files": [],
            }
        elif line.strip() and current is not None:
            parts = line.split("\t")
            if len(parts) == 3:
                ins, dele, path = parts
                if _dropped(path, exclude):
                    continue
                current["files"].append(
                    (
                        int(ins) if ins.isdigit() else 0,
                        int(dele) if dele.isdigit() else 0,
                        path,
                    )
                )
    if current:
        commits.append(current)

    weeks: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    types: collections.Counter = collections.Counter()
    type_ins: collections.Counter = collections.Counter()
    authors: dict[str, collections.Counter] = collections.defaultdict(
        collections.Counter
    )
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
        dates=sorted(
            datetime.fromisoformat(c["date"]).strftime("%Y-%m-%d") for c in commits
        ),
        n_commits=len(commits),
        first=commits[-1]["date"] if commits else None,
        last=commits[0]["date"] if commits else None,
        active_days=len(days),
        weeks={k: dict(v) for k, v in sorted(weeks.items())},
        types={k: {"commits": v, "ins": type_ins[k]} for k, v in types.most_common()},
        authors={
            k: dict(v)
            for k, v in sorted(authors.items(), key=lambda kv: -kv[1]["commits"])
        },
        churn={
            k: dict(v) for k, v in sorted(churn.items(), key=lambda kv: -kv[1]["ins"])
        },
        loc={k: dict(v) for k, v in sorted(loc.items(), key=lambda kv: -kv[1]["code"])},
        langs={k: {"lines": v, "files": lang_files[k]} for k, v in langs.most_common()},
        excluded=exclude,
    )
