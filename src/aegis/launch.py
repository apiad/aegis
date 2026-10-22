"""Every process aegis starts, started here (#548).

No other module starts a process, by ``subprocess``, asyncio's spawns, the
``os`` and ``pty`` process calls, ``webbrowser`` or ``multiprocessing``, whether
it calls them or hands them to an executor; ``tests/test_launch_only.py`` fails
if one does. The multi-user mode switches each child to its owner's Linux user
and drops its capabilities, and that has to happen for every process or it
protects nothing (docs/superpowers/specs/2026-10-21-linux-users-design.html,
section 06), so there is one place to do it.

A caller picks one of five shapes and keeps its own reading, timeout and kill
logic:

- ``child``: async, with pipes, optionally in a session's cgroup with the OOM
  score raised. Harnesses, monitors, workflow steps, one-shot runs, probes.
- ``pty``: async, on a terminal that becomes the child's controlling terminal.
- ``run``: sync, output captured, with a timeout. git, gh, systemctl.
- ``detach``: sync, in a session of its own, never waited for.
- ``browser``: a URL handed to the desktop's browser through ``webbrowser``,
  which starts its own process.

``asyncio.create_subprocess_exec`` and ``subprocess.run``/``Popen`` are looked
up on their modules at each call, so a test that patches them reaches every
process.
"""

from __future__ import annotations

import asyncio
import subprocess
import webbrowser
from pathlib import Path

from .cgroups import wrap
from .harness import raise_oom_score

PIPE = asyncio.subprocess.PIPE
DEVNULL = asyncio.subprocess.DEVNULL
STDOUT = asyncio.subprocess.STDOUT
# asyncio.create_subprocess_exec's own default for a pipe's line limit.
LIMIT = 2**16


async def child(
    argv: list[str],
    *,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    stdin=None,
    stdout=None,
    stderr=None,
    cgroup: Path | None = None,
    oom: bool = False,
    new_session: bool = False,
    limit: int = LIMIT,
) -> asyncio.subprocess.Process:
    """``argv`` started inside ``cgroup`` when there is one. ``env`` None is
    the server's environment. Raises FileNotFoundError for a missing binary,
    with or without the cgroup."""
    proc = await asyncio.create_subprocess_exec(
        *wrap(cgroup, argv),
        cwd=cwd,
        env=env,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        start_new_session=new_session,
        limit=limit,
    )
    if oom:
        raise_oom_score(proc.pid)
    return proc


async def pty(
    argv: list[str],
    *,
    tty: int,
    cwd: str | Path,
    env: dict[str, str] | None,
    cgroup: Path | None = None,
) -> asyncio.subprocess.Process:
    """``argv`` on the terminal ``tty``, as its controlling terminal
    (``setsid --ctty``: a preexec_fn is unsafe in a threaded server), in
    ``cgroup`` when there is one, with the OOM score raised."""
    proc = await asyncio.create_subprocess_exec(
        *wrap(cgroup, ["setsid", "--ctty", *argv]),
        stdin=tty,
        stdout=tty,
        stderr=tty,
        cwd=cwd,
        env=env,
    )
    raise_oom_score(proc.pid)
    return proc


def run(
    argv: list[str],
    *,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    input: bytes | str | None = None,
    timeout: float | None = None,
    text: bool = False,
    errors: str | None = None,
) -> subprocess.CompletedProcess:
    """``argv`` run to its end with its output captured. OSError and
    subprocess.TimeoutExpired reach the caller, as from subprocess.run."""
    return subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        input=input,
        capture_output=True,
        timeout=timeout,
        text=text,
        errors=errors,
    )


def detach(
    argv: list[str],
    *,
    cwd: str | Path | None = None,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
) -> subprocess.Popen:
    """``argv`` in a session of its own, with no stdin, not waited for."""
    return subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=stdout,
        stderr=stderr,
        start_new_session=True,
        cwd=cwd,
    )


def browser(url: str) -> None:
    """``url`` opened by ``webbrowser``, which picks the desktop's browser and
    starts it itself."""
    webbrowser.open(url)
