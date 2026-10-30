"""Every process aegis starts, started here (#548).

No other module starts a process, by ``subprocess``, asyncio's spawns, the
``os`` and ``pty`` process calls, ``webbrowser`` or ``multiprocessing``, whether
it calls them or hands them to an executor; ``tests/test_launch_only.py`` fails
if one does. The multi-user mode switches each child to its owner's Linux user
and drops its capabilities, and that has to happen for every process or it
protects nothing (docs/superpowers/specs/2026-10-21-linux-users-design.html,
section 06), so there is one place to do it.

A caller picks one of five shapes and keeps its own reading and timeout
logic. Process signals use the shared helper below, so a server that cannot
signal a member's process retries as that member:

- ``child``: async, with pipes, optionally in a session's cgroup with the OOM
  score raised. Harnesses, monitors, workflow steps, one-shot runs, probes.
- ``pty``: async, on a terminal that becomes the child's controlling terminal.
- ``run``: sync, output captured, with a timeout. git, gh, systemctl.
- ``detach``: sync, in a session of its own, never waited for.
- ``browser``: a URL handed to the desktop's browser through ``webbrowser``,
  which starts its own process.

``child`` and ``pty`` also take ``via``, the linked server the process reaches
the internet through (exit.py), or None for this machine's own route. It is
required, like ``user``: a process that should exit and forgets to say would
leave from the address the person meant to avoid. ``resolve_via`` gives what
the configuration says. ``run`` has no ``via``: it is the server's housekeeping,
``git`` and ``systemctl``, on local state.

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
import os
import pwd
import signal
import shutil
import subprocess
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import users
from .cgroups import wrap
from .harness import raise_oom_score

PIPE = asyncio.subprocess.PIPE
DEVNULL = asyncio.subprocess.DEVNULL
STDOUT = asyncio.subprocess.STDOUT
# asyncio.create_subprocess_exec's own default for a pipe's line limit.
LIMIT = 2**16
# die_with_parent: go on only while the parent is the server that started it.
_STILL_PARENT = '[ "$PPID" = "$0" ] || exit 143; exec "$@"'

# The multi-user mode: its pool, setpriv's path and a login's PATH; None off.
_mode: tuple[users.Pool, str, str] | None = None


# The server's exits (exit.py) and how to read its configuration; None off.
_exits: Any = None
_resolve: Callable[[str | None], str | None] | None = None


def configure_exits(
    exits: Any, resolve: Callable[[str | None], str | None] | None
) -> None:
    global _exits, _resolve
    _exits, _resolve = exits, resolve


def exits_in_force() -> Any:
    return _exits


def resolve_via(agent: str | None = None) -> str | None:
    """The link a process started now exits through: the one ``agent`` names,
    or the server's, or None."""
    return None if _resolve is None else _resolve(agent)


def _via(via: str | None, env: dict[str, str] | None) -> dict[str, str] | None:
    if via is None or _exits is None:
        return env
    base = os.environ if env is None else env
    # Git's own choice of ssh stays: it may carry a key the person needs. A
    # person who set one chose it, and it goes direct like a bare ``ssh``.
    keep = (
        {"GIT_SSH_COMMAND": base["GIT_SSH_COMMAND"]}
        if "GIT_SSH_COMMAND" in base
        else {}
    )
    return {**base, **_exits.env(via), **keep}


def exit_proxy(via: str | None) -> dict[str, str] | None:
    """The exit for a program that takes its proxy as a setting: Chromium."""
    return None if via is None or _exits is None else _exits.get(via).playwright_proxy()


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
    via: str | None,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    stdin=None,
    stdout=None,
    stderr=None,
    cgroup: Path | None = None,
    oom: bool = False,
    new_session: bool = False,
    die_with_parent: bool = False,
    limit: int = LIMIT,
) -> asyncio.subprocess.Process:
    """``argv`` started inside ``cgroup`` when there is one. ``env`` None is
    the server's environment. Raises FileNotFoundError for a missing binary,
    with or without the cgroup.

    ``die_with_parent`` has the kernel send the child SIGTERM when the thread
    that started it ends, through ``setpriv --pdeathsig`` (a preexec_fn is
    unsafe in a threaded server): for a child that is no use to anyone once
    its parent is gone, such as an OpenCode server (#620). A parent that ended
    before setpriv set the signal sends none, so a shell checks the parent
    after it and then execs the program, whose pid stays the child's. Without
    setpriv on PATH the child starts as it would without the flag."""
    setpriv = shutil.which("setpriv") if die_with_parent else None
    if setpriv is not None:
        exe = shutil.which(argv[0])
        if exe is None:
            raise FileNotFoundError(argv[0])
        argv = [setpriv, "--pdeathsig", "TERM", "/bin/sh", "-c", _STILL_PARENT,
                str(os.getpid()), exe, *argv[1:]]  # fmt: skip
    argv, env, cwd = _as(user, argv, _via(via, env), cwd)
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
    via: str | None,
    tty: int,
    cwd: str | Path,
    env: dict[str, str] | None,
    cgroup: Path | None = None,
) -> asyncio.subprocess.Process:
    """``argv`` on the terminal ``tty``, as its controlling terminal
    (``setsid --ctty``: a preexec_fn is unsafe in a threaded server), in
    ``cgroup`` when there is one, with the OOM score raised."""
    argv, env, start_in = _as(user, argv, _via(via, env), cwd)
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


def _signal_direct(pid: int, sig: int, group: bool) -> bool:
    """Send ``sig`` directly; return False only when permissions refuse it."""
    try:
        (os.killpg if group else os.kill)(pid, sig)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return True


def _signal_via_launcher(
    pid: int, sig: int, user: str | None, group: bool, denied: PermissionError
) -> None:
    name = signal.Signals(sig).name.removeprefix("SIG")
    target = f"-{pid}" if group else str(pid)
    argv = ["kill", "-s", name, "--", target]
    try:
        result = run(argv, user=user, timeout=5, text=True)
    except (OSError, subprocess.SubprocessError) as e:
        raise PermissionError(
            f"could not send {name} to {'process group' if group else 'process'} "
            f"{pid} as {user}: {e}"
        ) from e
    if result.returncode:
        try:
            (os.killpg if group else os.kill)(pid, 0)
        except ProcessLookupError:
            return  # it exited between the server's attempt and the retry
        except PermissionError:
            pass
        detail = (result.stderr or "").strip()
        raise PermissionError(
            f"could not send {name} to {'process group' if group else 'process'} "
            f"{pid} as {user}: {detail or f'kill exited {result.returncode}'}"
        ) from denied


def signal_process(
    pid: int, sig: int, *, user: str | None, group: bool = False
) -> None:
    """Signal a child or process group, retrying through the launcher as its
    owner when the server lacks permission. With no owner, the retry uses the
    server's user. ``pid`` must be positive; missing processes are stopped."""
    if pid <= 0:
        raise ValueError("pid must be positive")
    if _signal_direct(pid, sig, group):
        return
    _signal_via_launcher(
        pid, sig, user, group, PermissionError(f"server cannot signal pid {pid}")
    )


async def signal_process_async(
    pid: int, sig: int, *, user: str | None, group: bool = False
) -> None:
    """Async form: direct signals happen before yielding; a permission
    fallback runs off the event loop."""
    if pid <= 0:
        raise ValueError("pid must be positive")
    if _signal_direct(pid, sig, group):
        return
    denied = PermissionError(f"server cannot signal pid {pid}")
    await asyncio.to_thread(_signal_via_launcher, pid, sig, user, group, denied)


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
