"""A branch's pull requests from ``gh``, cached, and nothing when gh is missing,
not logged in, or the repo is not on GitHub."""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time

from .. import launch

TTL = 120.0
TIMEOUT = 8.0

_cache: dict[tuple[str, str], tuple[float, list[dict] | None]] = {}
# Builds run in threads and forget() on the loop.
_lock = threading.Lock()


def _ci(rollup) -> str:
    states = [
        (c.get("conclusion") or c.get("state") or c.get("status") or "").upper()
        for c in rollup or []
    ]
    if not states:
        return ""
    if any(s in ("FAILURE", "ERROR", "CANCELLED", "TIMED_OUT") for s in states):
        return "CI failing"
    if all(s in ("SUCCESS", "NEUTRAL", "SKIPPED") for s in states):
        return "CI passing"
    return "CI running"


def prs(top: str, branch: str) -> list[dict] | None:
    """``[{n, url, title, state, ci}]`` for PRs whose head is ``branch``, newest
    first; ``None`` when gh could not answer."""
    key = (top, branch)
    with _lock:
        hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < TTL:
        return hit[1]
    found = None
    if branch and shutil.which("gh"):
        try:
            r = launch.run(
                [
                    "gh",
                    "pr",
                    "list",
                    "--head",
                    branch,
                    "--state",
                    "all",
                    "--limit",
                    "20",
                    "--json",
                    "number,title,state,url,statusCheckRollup",
                ],
                cwd=top,
                text=True,
                errors="replace",
                timeout=TIMEOUT,
                user=None,
            )
            if r.returncode == 0:
                found = [
                    {
                        "n": p["number"],
                        "url": p["url"],
                        "title": p.get("title", ""),
                        "state": p.get("state", "").lower(),
                        "ci": _ci(p.get("statusCheckRollup")),
                    }
                    for p in json.loads(r.stdout or "[]")
                ]
        except (OSError, subprocess.TimeoutExpired, ValueError, KeyError):
            found = None
    with _lock:
        _cache[key] = (time.monotonic(), found)
    return found


def forget(top: str) -> None:
    """Drop what is cached for a worktree: a PR was just opened or merged there."""
    with _lock:
        for k in [k for k in _cache if k[0] == top]:
            del _cache[k]
