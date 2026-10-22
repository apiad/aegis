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

Every shape takes ``user``, the Linux user the process acts for, or None for
the server itself. It is required, so a new call site has to say. Outside the
multi-user mode it changes nothing. Once ``configure`` turns the mode on (at
boot, app.py), every process starts behind ``setpriv``, which drops every
capability, and a process with a user is switched to it with that user's own
environment, what the caller added and the pool file (users.py). ``detach``
and ``browser`` open things on the server's desktop, which a shared server
does not have, and are refused in the mode.

``asyncio.create_subprocess_exec`` and ``subprocess.run``/``Popen`` are looked
up on their modules at each call, so a test that patches them reaches every
process.
"""

from __future__ import annotations

import asyncio
import pwd
import shutil
import subprocess
import webbrowser
from pathlib import Path

from . import users
from .cgroups import wrap
from .harness import raise_oom_score

PIPE = asyncio.subprocess.PIPE
DEVNULL = asyncio.subprocess.DEVNULL
STDOUT = asyncio.subprocess.STDOUT
# asyncio.create_subprocess_exec's own default for a pipe's line limit.
LIMIT = 2**16

# The multi-user mode: its pool, setpriv's path and a login's PATH; None off.
_mode: tuple[users.Pool, str, str] | None = None


def configure(mu: users.MultiUser | None) -> None:
    """Turn the multi-user mode on for every process from now on, or off."""
    global _mode
    if mu is None:
        _mode = None
        return
    setpriv = shutil.which("setpriv") or "setpriv"
    _mode = (users.Pool(mu.env_file), setpriv, users.env_path())


def _as(
    user: str | None,
    argv: list[str],
    env: dict[str, str] | None,
    cwd: str | Path | None,
) -> tuple[list[str], dict[str, str] | None, str | Path | None]:
    """``argv``, ``env`` and ``cwd`` as the process starts: unchanged outside
    the multi-user mode; in it, behind setpriv, which drops every capability
    and, for a user, switches to it with that user's own environment. A bare
    program name is found in the server's PATH, since a login's PATH lacks
    the directories harnesses install to. A switched process enters its cwd
    after the switch, through a shell that then execs the program (so its pid
    stays the program's): the server may not be able to enter a member's
    home, and the kernel checks the member's own access instead."""
    if _mode is None:
        return argv, env, cwd
    pool, setpriv, path = _mode
    pw = None
    if user is not None:
        try:
            pw = pwd.getpwnam(user)
        except KeyError:
            raise PermissionError(f"Linux user {user} does not exist") from None
    exe = argv[0]
    if "/" not in exe:
        found = shutil.which(exe)
        if found is None:
            raise FileNotFoundError(exe)
        exe = found
    prefix = users.setpriv_argv(pw, setpriv)
    if pw is None:
        return [*prefix, exe, *argv[1:]], env, cwd
    enter = (
        [] if cwd is None else ["/bin/sh", "-c", 'cd -- "$0" && exec "$@"', str(cwd)]
    )
    env = users.user_env(pw, users.added(env), pool.get(), path)
    return [*prefix, *enter, exe, *argv[1:]], env, None


def _refused(what: str) -> None:
    if _mode is not None:
        raise PermissionError(f"{what} is off in multi-user mode")


async def child(
    argv: list[str],
    *,
    user: str | None,
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
    argv, env, cwd = _as(user, argv, env, cwd)
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
    user: str | None,
    tty: int,
    cwd: str | Path,
    env: dict[str, str] | None,
    cgroup: Path | None = None,
) -> asyncio.subprocess.Process:
    """``argv`` on the terminal ``tty``, as its controlling terminal
    (``setsid --ctty``: a preexec_fn is unsafe in a threaded server), in
    ``cgroup`` when there is one, with the OOM score raised."""
    argv, env, start_in = _as(user, argv, env, cwd)
    proc = await asyncio.create_subprocess_exec(
        *wrap(cgroup, ["setsid", "--ctty", *argv]),
        stdin=tty,
        stdout=tty,
        stderr=tty,
        cwd=start_in,
        env=env,
    )
    raise_oom_score(proc.pid)
    return proc


def run(
    argv: list[str],
    *,
    user: str | None,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    input: bytes | str | None = None,
    timeout: float | None = None,
    text: bool = False,
    errors: str | None = None,
) -> subprocess.CompletedProcess:
    """``argv`` run to its end with its output captured. OSError and
    subprocess.TimeoutExpired reach the caller, as from subprocess.run."""
    argv, env, cwd = _as(user, argv, env, cwd)
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
    user: str | None,
    cwd: str | Path | None = None,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
) -> subprocess.Popen:
    """``argv`` in a session of its own, with no stdin, not waited for."""
    _refused("starting a program on the server's desktop")
    return subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=stdout,
        stderr=stderr,
        start_new_session=True,
        cwd=cwd,
    )


def browser(url: str, *, user: str | None) -> None:
    """``url`` opened by ``webbrowser``, which picks the desktop's browser and
    starts it itself. ``user`` is for symmetry: refused in the mode."""
    _refused("opening a browser on the server's desktop")
    webbrowser.open(url)
