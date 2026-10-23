"""The doctor's last findings, kept for the new tab and /doctor.

A run probes every harness the config names, 0.5 s to 20 s, so the new tab
never waits on one: it reads what the last run found. A run happens in the
background ``FIRST_AFTER_S`` after the start, at once whenever ``.aegis.yaml``
changes, and every ``EVERY_S``, since a harness is upgraded outside aegis.
/doctor asks for a fresh one. One run at a time; a run asked for during
another waits for it and then runs.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

from .config import Finding

log = logging.getLogger("aegis.doctor_watch")

FIRST_AFTER_S = 10.0
EVERY_S = 6 * 3600.0


class DoctorWatch:
    def __init__(
        self,
        run: Callable[[], Awaitable[list[Finding]]],
        first_after: float = FIRST_AFTER_S,
        every: float = EVERY_S,
    ) -> None:
        self._run = run
        self._first_after = first_after
        self._every = every
        self._lock = asyncio.Lock()
        self._kicked = asyncio.Event()
        self._task: asyncio.Task | None = None
        self.findings: list[Finding] = []

    def problems(self) -> list[Finding]:
        """The last run's warnings and errors, errors first."""
        bad = [f for f in self.findings if f.level != "ok"]
        return sorted(bad, key=lambda f: f.level != "error")

    async def refresh(self) -> list[Finding]:
        async with self._lock:
            self.findings = await self._run()
            return self.findings

    def kick(self) -> None:
        """The config changed: run again soon."""
        self._kicked.set()

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _loop(self) -> None:
        wait = self._first_after
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._kicked.wait(), wait)
            self._kicked.clear()
            try:
                await self.refresh()
            except Exception:
                log.exception("aegis: the doctor's background run failed")
            wait = self._every
