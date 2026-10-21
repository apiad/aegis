"""One worktree's git state, read with a few git calls that each time out.

Every failure degrades to ``None`` or an empty answer: a repo mid-rebase or a
hung ``git status`` must cost the panel a stale line, never an error. Nothing
here runs on the event loop; the service calls it through a thread.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field

from .. import launch

TIMEOUT = 3.0
SLUG = re.compile(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?(?:/|$)")

# Marker in the git dir → the operation it means. porcelain=v2 does not report
# an operation in progress, so it is read off disk.
OPS = (
    ("rebase-merge", "rebase"),
    ("rebase-apply", "rebase"),
    ("MERGE_HEAD", "merge"),
    ("CHERRY_PICK_HEAD", "cherry-pick"),
    ("BISECT_LOG", "bisect"),
)


@dataclass
class Probe:
    branch: str = ""
    upstream: str = ""
    detached: bool = False
    op: str = ""
    # path inside the worktree → "+a −d", "new" or "deleted"
    dirty: dict[str, str] = field(default_factory=dict)
    # "owner/repo" of the origin remote on GitHub, else ""
    slug: str = ""


def git(top: str, *args: str) -> str | None:
    try:
        r = launch.run(
            ["git", "-C", top, *args],
            text=True,
            errors="surrogateescape",  # a path git prints may not be UTF-8
            timeout=TIMEOUT,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    return r.stdout if r.returncode == 0 else None


def _op(top: str) -> str:
    gd = git(top, "rev-parse", "--absolute-git-dir")
    if not gd:
        return ""
    gd = gd.strip()
    return next((name for m, name in OPS if os.path.exists(os.path.join(gd, m))), "")


def probe(top: str) -> Probe | None:
    """The worktree's branch, upstream, operation and changed files; ``None``
    when it is gone or git cannot read it."""
    if not os.path.isdir(top):
        return None
    out = git(
        top, "status", "--porcelain=v2", "--branch", "-z", "--untracked-files=all"
    )
    if out is None:
        return None
    p = Probe(op=_op(top), slug=slug(git(top, "remote", "get-url", "origin") or ""))
    fields = out.split("\0")
    i = 0
    while i < len(fields):
        line = fields[i]
        i += 1
        if not line:
            continue
        if line.startswith("# branch.head "):
            head = line.removeprefix("# branch.head ")
            p.detached = head == "(detached)"
            p.branch = "" if p.detached else head
        elif line.startswith("# branch.upstream "):
            p.upstream = line.removeprefix("# branch.upstream ")
        elif line.startswith("? "):
            p.dirty[line[2:]] = "new"
        elif line[:2] in ("1 ", "u "):
            parts = line.split(" ", 8 if line[0] == "1" else 10)
            xy, path = parts[1], parts[-1]
            p.dirty[path] = "deleted" if "D" in xy else ("new" if "A" in xy else "")
        elif line.startswith("2 "):
            p.dirty[line.split(" ", 9)[-1]] = ""
            i += 1  # the rename's original path is the next field
    stat = git(top, "diff", "--numstat", "HEAD", "--") or ""
    for row in stat.splitlines():
        a, d, path = (row.split("\t", 2) + ["", ""])[:3]
        if path in p.dirty and not p.dirty[path]:
            p.dirty[path] = f"+{a} −{d}" if a != "-" else "binary"
    for path, s in p.dirty.items():
        p.dirty[path] = s or "changed"
    return p


def base(top: str, upstream: str) -> str:
    """What "upstream" means for a worktree: its branch's upstream while that
    ref exists, else the remote's default branch. A PR merged by rebase with its
    branch deleted leaves a worktree whose upstream is gone."""
    if upstream and git(top, "rev-parse", "--verify", "--quiet", upstream) is not None:
        return upstream
    head = git(top, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    if head:
        return head.strip()
    for ref in ("origin/main", "origin/master"):
        if git(top, "rev-parse", "--verify", "--quiet", ref) is not None:
            return ref
    return ""


def slug(url: str) -> str:
    """ "owner/repo" from a GitHub remote or PR URL, else ""."""
    m = SLUG.search(url.strip())
    return f"{m[1]}/{m[2]}" if m else ""


def unpushed(top: str, shas: list[str], upstream: str) -> list[str] | None:
    """Of ``shas``, those not yet upstream, in their given order. A sha counts
    as pushed when a remote-tracking ref contains it, or when an equivalent
    patch is on ``upstream`` (a rebase merge rewrote it). A sha the repo no
    longer has (amended, rebased away) is dropped. ``None`` when git could not
    tell: never read as "all pushed"."""
    if not shas:
        return []
    out = git(top, "rev-list", "--ignore-missing", *shas, "--not", "--remotes")
    if out is None:
        return None
    walked = set(out.split())
    left = [s for s in shas if s in walked]
    if not left or not upstream:
        return left
    seen: set[str] = set()
    equal: set[str] = set()
    for tip in left:
        if tip in seen:
            continue
        # Every commit between the merge base and tip, "-" when upstream has
        # an equivalent patch: one call covers a whole branch.
        cherry = git(top, "cherry", upstream, tip)
        if cherry is None:
            return None
        for ln in cherry.splitlines():
            seen.add(ln[2:])
            if ln.startswith("- "):
                equal.add(ln[2:])
    return [s for s in left if s not in equal]
