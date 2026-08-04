"""The process-launch seam shared by every harness driver.

Both driver families converge on the same shape — an argv, a cwd, an
optional env, and three pipes. ``Launcher`` is that shape as an
interface, so remoteness lives in one place instead of once per driver.
"""
from __future__ import annotations

import asyncio
from typing import Protocol, runtime_checkable

# claude stream-json and ACP JSON-RPC both put whole tool payloads on one
# line; asyncio's 64 KiB default is far too small. Mirrors
# ``drivers.claude._STREAM_LIMIT``.
STREAM_LIMIT = 16 * 1024 * 1024


@runtime_checkable
class Launcher(Protocol):
    """Starts a harness process somewhere and hands back its pipes."""

    host_key: str
    local_root: str | None

    async def spawn(self, argv: list[str], *, cwd: str,
                    env: dict[str, str] | None
                    ) -> asyncio.subprocess.Process: ...

    def persona_root(self, cwd: str) -> str: ...


class LocalLauncher:
    """Today's behaviour, unchanged: exec here, in this process tree."""

    host_key = "local"

    def __init__(self, local_root: str | None = None) -> None:
        self.local_root = local_root

    def persona_root(self, cwd: str) -> str:
        """Where a relative persona path resolves.

        A persona file always lives in the LOCAL project, even when the
        harness runs on another box — so drivers resolve it against this
        rather than against the (possibly remote) session cwd. Falling
        back to ``cwd`` keeps pre-existing local behaviour identical.
        """
        return self.local_root or cwd

    async def spawn(self, argv: list[str], *, cwd: str,
                    env: dict[str, str] | None
                    ) -> asyncio.subprocess.Process:
        kw: dict = dict(
            cwd=cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=STREAM_LIMIT,
        )
        if env is not None:
            kw["env"] = env
        return await asyncio.create_subprocess_exec(*argv, **kw)


LOCAL = LocalLauncher()
