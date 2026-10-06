"""Which aegis is running, and the newest release on PyPI.

A build from git reports the last release number from its metadata, so that
number alone cannot tell two dev builds apart. The commit comes from PEP 610's
``direct_url.json``, which pip and uv write for an install from a VCS URL
(``uvx --from git+…@<ref>``), or from ``git`` in the source tree of an editable
install. Either one makes the build ``dev``.

The latest release is fetched from PyPI at most once an hour, and retried after
ten minutes when the fetch failed. A failure is ``None``, never an error: an
offline server works as before. ``AEGIS_RELEASES_URL`` points the fetch
elsewhere, which keeps the tests off the network.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import time
import urllib.request
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

DIST = "aegis-harness"
PYPI_URL = f"https://pypi.org/pypi/{DIST}/json"
FETCH_TIMEOUT_S = 5.0
FRESH_S = 3600.0
RETRY_S = 600.0


def _git(cwd: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def running() -> dict:
    """``version`` is the release number in the metadata; ``commit`` and ``ref``
    are set only for a build from git, which is then ``dev``."""
    try:
        dist = distribution(DIST)
    except PackageNotFoundError:
        return {"version": None, "commit": None, "ref": None, "dev": True}
    info = {"version": dist.version, "commit": None, "ref": None, "dev": False}
    raw = dist.read_text("direct_url.json")
    if not raw:
        return info
    direct = json.loads(raw)
    if vcs := direct.get("vcs_info"):
        info.update(
            commit=vcs.get("commit_id"), ref=vcs.get("requested_revision"), dev=True
        )
    elif direct.get("dir_info", {}).get("editable"):
        src = Path(urllib.request.url2pathname(direct["url"].removeprefix("file://")))
        ref = _git(src, "rev-parse", "--abbrev-ref", "HEAD")
        info.update(
            commit=_git(src, "rev-parse", "HEAD"),
            ref=None if ref == "HEAD" else ref,
            dev=True,
        )
    return info


def _fetch(url: str) -> str | None:
    try:
        with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT_S) as r:
            version = json.load(r)["info"]["version"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return version if isinstance(version, str) else None


def _numbers(v: str) -> tuple[int, ...] | None:
    m = re.fullmatch(r"(\d+(?:\.\d+)*)", v)
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def status(run: dict, latest: str | None) -> str:
    """``dev``, ``unknown``, ``behind`` or ``current``."""
    if run["dev"]:
        return "dev"
    if latest is None or run["version"] is None:
        return "unknown"
    have, want = _numbers(run["version"]), _numbers(latest)
    if have is None or want is None:
        return "current" if run["version"] == latest else "unknown"
    return "behind" if have < want else "current"


class Versions:
    def __init__(self) -> None:
        self.running = running()
        self._latest: str | None = None
        self._checked = 0.0
        self._lock = asyncio.Lock()

    async def latest(self) -> str | None:
        async with self._lock:
            age = time.monotonic() - self._checked
            if self._checked and age < (FRESH_S if self._latest else RETRY_S):
                return self._latest
            url = os.environ.get("AEGIS_RELEASES_URL", PYPI_URL)
            self._latest = await asyncio.to_thread(_fetch, url) or self._latest
            self._checked = time.monotonic()
            return self._latest

    async def wire(self) -> dict:
        latest = await self.latest()
        return {
            "running": self.running,
            "latest": latest,
            "status": status(self.running, latest),
        }
