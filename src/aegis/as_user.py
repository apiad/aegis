"""File code run with one Linux user's credentials (#555).

In multi-user mode the server reads and writes files for people: the Files
view, the editor, a ticket's bytes, a sent file, an attachment, an artifact
draft. Each of those must be checked by the kernel against the person's own
user, never the server's (docs/superpowers/specs/2026-10-21-linux-users-design.html,
section 07). Linux keeps a filesystem uid and gid per thread, used for every
permission check and for the owner of every file created, and the setgroups
syscall, called raw rather than through libc (which repeats it on every
thread), changes the calling thread's groups only. So each user gets a pool
of threads that take its credentials when they start and keep them for life;
the functions they run are the same ones the server runs for itself, and a
Runner for no user is plain ``asyncio.to_thread``.

A process started from such a thread does not inherit them: execve resets the
filesystem ids to the effective ones. Code on a runner that starts git passes
the user to ``launch``, as any other caller does.

Taking another user's ids needs CAP_SETUID and CAP_SETGID, which the server
holds only in multi-user mode (users.boot_problems).
"""

from __future__ import annotations

import asyncio
import ctypes
import os
import platform
import pwd
from collections.abc import Callable
from concurrent.futures import BrokenExecutor, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from .users import SETGROUPS

T = TypeVar("T")

_libc = ctypes.CDLL(None, use_errno=True)


def become(pw: pwd.struct_passwd) -> None:
    """Give the calling thread ``pw``'s groups and filesystem ids."""
    groups = os.getgrouplist(pw.pw_name, pw.pw_gid)
    arr = (ctypes.c_uint * len(groups))(*groups)
    if _libc.syscall(SETGROUPS[platform.machine()], len(groups), arr) != 0:
        raise PermissionError(f"setgroups for {pw.pw_name}: errno {ctypes.get_errno()}")
    _libc.setfsgid(pw.pw_gid)
    _libc.setfsuid(pw.pw_uid)
    # Both return the previous id and never fail; asking again says what holds.
    if _libc.setfsuid(-1) != pw.pw_uid or _libc.setfsgid(-1) != pw.pw_gid:
        raise PermissionError(f"cannot take {pw.pw_name}'s file credentials")


class Runner:
    """Runs a blocking function as ``user``, or as the server for None."""

    def __init__(self, user: str | None, pool: ThreadPoolExecutor | None = None):
        self.user = user
        self._pool = pool

    async def __call__(self, fn: Callable[..., T], *args: Any, **kw: Any) -> T:
        if self._pool is None:
            return await asyncio.to_thread(fn, *args, **kw)
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(self._pool, lambda: fn(*args, **kw))
        except BrokenExecutor:
            raise PermissionError(
                f"cannot act as Linux user {self.user}; see the server's log"
            ) from None

    def call(self, fn: Callable[..., T], *args: Any, **kw: Any) -> T:
        """``fn`` run as ``user`` from a thread that is not the event loop's,
        waiting for it."""
        if self._pool is None:
            return fn(*args, **kw)
        try:
            return self._pool.submit(fn, *args, **kw).result()
        except BrokenExecutor:
            raise PermissionError(
                f"cannot act as Linux user {self.user}; see the server's log"
            ) from None


class Runners:
    """One Runner per user, made on first use, for the server's whole run."""

    def __init__(self) -> None:
        self._by: dict[str, Runner] = {}
        self._server = Runner(None)

    def get(self, user: str | None) -> Runner:
        if user is None:
            return self._server
        if user not in self._by:
            try:
                pw = pwd.getpwnam(user)
            except KeyError:
                raise PermissionError(f"Linux user {user} does not exist") from None
            pool = ThreadPoolExecutor(
                max_workers=4,
                thread_name_prefix=f"as-{user}",
                initializer=become,
                initargs=(pw,),
            )
            self._by[user] = Runner(user, pool)
        return self._by[user]

    def shutdown(self) -> None:
        for r in self._by.values():
            assert r._pool is not None
            r._pool.shutdown(wait=False, cancel_futures=True)
        self._by.clear()


@dataclass(frozen=True)
class Place:
    """Where one person's files are: the root their Files view shows, the
    folder it never reaches (fs_paths.resolve's ``state``), the folder whose
    ``trash/`` takes their deletes (fs_trash), and the runner that opens
    them."""

    root: Path
    guard: Path
    trash: Path
    run: Runner

    @property
    def user(self) -> str | None:
        return self.run.user
