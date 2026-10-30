"""The harness versions this release was tested with, and a judgment of the one installed.

``data/tested-harnesses.toml`` holds, per harness, the npm package and the
lowest and highest version the canary passed at. The release moves the two
ends (know-how/releasing.md); ``scripts/canary.sh`` installs either of them.

A version is the first ``\\d+(\\.\\d+)+`` in ``--version`` output, compared as a
tuple of ints. A pre-release suffix right after it (``-`` or ``+`` then a letter
or digit) counts as outside the range, and so does output with no version in
it: the message says it could not be read. A verdict is ``in_range``,
``accepted`` (listed under ``harness_versions.accepted``), ``newer``, ``older``
or ``unknown``; the last three are outside.

The read runs ``bin --version`` with a 5 s timeout and caches the answer by the
binary's resolved path and mtime, so a spawn pays for it only after an upgrade,
which replaces the file. A failed read is kept for a minute, so a binary that
hangs costs one timeout a minute, not one per launch.

Every message names the binary as its resolved path, so a symlink on PATH shows
where it really points, and the command that installs the tested max with the
installer the path says put it there. The max is the target whether the
installed version is newer or older: it is the newest version known to work.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import signal
import time
import tomllib
from dataclasses import dataclass
from importlib import resources
from typing import TYPE_CHECKING

from . import launch

if TYPE_CHECKING:
    from .harness import Harness

# `cursor-agent --version` needs 8.1 s to answer (measured 2026-10-19: a node
# bundle's cold start), so 5 s made its version unreadable forever — every
# person's launch asked, and "always" could not settle it because a version
# that was never read can never be accepted.
VERSION_TIMEOUT_S = 30.0
OUTSIDE = ("newer", "older", "unknown")
_VERSION = re.compile(r"(\d+(?:\.\d+)+)([-+][0-9A-Za-z])?")


@dataclass(frozen=True)
class Range:
    harness: str
    package: str
    min: tuple[int, ...]
    max: tuple[int, ...]
    min_s: str
    max_s: str
    hint: str | None = None

    def text(self) -> str:
        return self.min_s if self.min == self.max else f"{self.min_s} to {self.max_s}"


_ranges: dict[str, Range] | None = None


def ranges() -> dict[str, Range]:
    global _ranges
    if _ranges is None:
        raw = tomllib.loads(
            resources.files("aegis").joinpath("data/tested-harnesses.toml").read_text()
        )
        _ranges = {
            name: Range(
                name,
                t["package"],
                _ints(t["min"]),
                _ints(t["max"]),
                t["min"],
                t["max"],
                t.get("hint"),
            )
            for name, t in raw.items()
        }
    return _ranges


def _ints(s: str) -> tuple[int, ...]:
    return tuple(int(p) for p in s.split("."))


def parse_version(text: str) -> tuple[tuple[int, ...] | None, str | None]:
    """(the version as ints, as written); ints are None for a pre-release or no match."""
    m = _VERSION.search(text)
    if m is None:
        return None, None
    if m.group(2):
        end = re.match(r"[-+.0-9A-Za-z]*", text[m.start() :])
        return None, end.group(0) if end else m.group(1)
    return _ints(m.group(1)), m.group(1)


# A read's answer by (the name run, its resolved path, mtime): an upgrade
# replaces the file, so the key moves with it. A failed read is kept for
# FAILED_TTL_S only, so a binary that hangs on --version costs one timeout a
# minute, not one per launch, and a transient failure does not stick.
FAILED_TTL_S = 60.0
_cache: dict[tuple[str, str, int], tuple[str | None, float]] = {}
_inflight: dict[tuple[str, str, int], asyncio.Future[str | None]] = {}


async def read_version(bin: str) -> tuple[str | None, str | None]:
    """(the line of ``bin --version`` that holds a version, else its first
    line; the resolved path). The name found on PATH is what runs, so a shim
    that dispatches on its own name answers; the resolved path names the
    installer. Only stdout is read: a runtime's warning on stderr is not the
    version."""
    found = shutil.which(bin)
    if found is None:
        return None, None
    path = os.path.realpath(found)
    try:
        key = (found, path, os.stat(path).st_mtime_ns)
    except OSError:
        return None, path
    if (hit := _cache.get(key)) is not None:
        raw, at = hit
        if raw is not None or time.monotonic() - at < FAILED_TTL_S:
            return raw, path
    # Launches that start together (a boot's continues, the startup check)
    # share one read of a binary instead of running it once each.
    task = _inflight.get(key)
    if task is None:
        task = _inflight[key] = asyncio.ensure_future(_run(found))
        task.add_done_callback(lambda _: _inflight.pop(key, None))
    raw = await asyncio.shield(task)
    _cache[key] = (raw, time.monotonic())
    return raw, path


async def _run(exe: str) -> str | None:
    try:
        proc = await launch.child(
            [exe, "--version"],
            stdin=launch.DEVNULL,
            stdout=launch.PIPE,
            stderr=launch.DEVNULL,
            new_session=True,
            user=None,
            via=None,
        )
    except OSError:
        return None
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), VERSION_TIMEOUT_S)
    except TimeoutError:
        _kill(proc.pid)
        await proc.wait()
        return None
    except BaseException:
        _kill(proc.pid)  # cancelled with the server: leave no child behind
        raise
    lines = [ln.strip() for ln in out.decode(errors="replace").splitlines()]
    lines = [ln for ln in lines if ln]
    if proc.returncode or not lines:
        return None
    return next((ln for ln in lines if _VERSION.search(ln)), lines[0])


def _kill(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def install_command(
    r: Range, path: str | None, raw_version: str | None
) -> tuple[str, tuple[str, ...]]:
    """(the command that installs the tested max, notes to show under it)."""
    npm = f"npm install -g {r.package}@{r.max_s}"
    if r.harness == "cursor":
        return "curl https://cursor.com/install -fsS | bash", (
            "Cursor's own installer, which always installs its latest build;"
            " `npm install -g cursor-agent` is a different, unrelated package.",
        )
    p = path or ""
    if "/.local/share/claude/versions/" in p:
        return f"claude install {r.max_s}", (
            "The native build updates itself unless DISABLE_AUTOUPDATER=1 is set,"
            " so a pinned version does not stay pinned.",
        )
    if "/node_modules/" in p:
        if r.harness == "opencode" and "/node_modules/opencode-ai/" in p:
            return f"npm uninstall -g opencode-ai && {npm}", (
                "The old opencode-ai package must go first,"
                " or its opencode binary shadows the new one.",
            )
        return npm, ()
    if any(d in p for d in ("/Cellar/", "/homebrew/", "/linuxbrew/")):
        m = re.search(r"/Cellar/([^/]+)/", p)
        formula = m.group(1) if m else os.path.basename(p)
        return f"brew upgrade {formula}", (
            "Homebrew installs its latest release,"
            " which may itself be outside the tested range.",
        )
    return npm, (
        "aegis does not recognise the installer that put it there;"
        " the command above uses npm.",
    )


@dataclass(frozen=True)
class Verdict:
    harness: str
    label: str
    state: str
    version: str | None
    path: str | None
    range: Range
    install: str
    notes: tuple[str, ...]

    @property
    def outside(self) -> bool:
        return self.state in OUTSIDE

    def headline(self) -> str:
        r = self.range.text()
        if self.state in ("newer", "older"):
            text = (
                f"{self.label} {self.version} is {self.state} than the versions"
                f" this aegis release was tested with ({r})."
                " aegis is not guaranteed to work with it."
            )
        elif self.state == "unknown" and self.version:
            text = (
                f"{self.label} {self.version} is not a release this aegis release"
                f" was tested with ({r}). aegis is not guaranteed to work with it."
            )
        elif self.state == "unknown":
            text = (
                f"The version of {self.label} could not be read."
                f" This aegis release was tested with {r}."
                " aegis is not guaranteed to work with it."
            )
        elif self.state == "accepted":
            text = (
                f"{self.label} {self.version} is outside the tested versions ({r}),"
                " and it was accepted."
            )
        else:
            text = f"{self.label} {self.version} is a tested version ({r})."
        if self.outside and self.range.hint:
            text += f" {self.range.hint}"
        return text

    def _details(self) -> list[str]:
        return [
            f"found: {self.path or 'nothing on PATH'}",
            f"to install the tested version: {self.install}",
            *self.notes,
        ]

    def message(self) -> str:
        return "\n".join([self.headline(), *self._details()])

    def notice(self, started_anyway: bool) -> str:
        head = self.headline()
        if started_anyway:
            # True of every unattended start, a person's parked wake, Continue
            # and /workflow included (#450).
            head += (
                " The session started without asking: aegis asks only when a"
                " person starts a session or resumes a stopped one."
            )
        return "\n".join([head, *self._details()])

    def wire(self) -> dict:
        return {
            "harness": self.harness,
            "label": self.label,
            "state": self.state,
            "version": self.version,
            "path": self.path,
            "min": self.range.min_s,
            "max": self.range.max_s,
            "install": self.install,
            "notes": list(self.notes),
            "message": self.message(),
        }


def judge(
    harness: str,
    label: str,
    raw: str | None,
    path: str | None,
    accepted: dict[str, list[str]],
) -> Verdict:
    r = ranges()[harness]
    parsed, written = parse_version(raw or "")
    if written is not None and written in accepted.get(harness, []):
        state = "accepted"
    elif parsed is None:
        state = "unknown"
    elif parsed > r.max:
        state = "newer"
    elif parsed < r.min:
        state = "older"
    else:
        state = "in_range"
    install, notes = install_command(r, path, written)
    return Verdict(harness, label, state, written, path, r, install, notes)


async def check(harness: Harness, accepted: dict[str, list[str]]) -> Verdict:
    raw, path = await read_version(harness.bin)
    return judge(harness.name, harness.label, raw, path, accepted)
