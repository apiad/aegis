"""A daemon that is not there must say so once, at a level someone sees.

`--no-autostart` makes a new state reachable: the web process is up, no daemon
is, and nothing will start one. The browser's socket is accepted and simply
draws nothing, so the tab is indistinguishable from a slow one. The operator's
only other window into it is `journalctl -u aegis-web`, and `aegis web` runs
uvicorn at `log_level="warning"`, which pins the root logger above info — so a
retry logged at info reaches nobody at either end.

The retries stay at info: a reconnect across a daemon restart is normal and is
what the relay exists to do. It is the *first* failure of a view that is news.

The handler goes on the relay's own logger rather than through caplog, for the
reason `test_session_generation_config` writes down: `aegis_log.open()` sets
`propagate = False` on the "aegis" logger and never restores it, so once any
earlier test in this worker has opened the log, nothing from here reaches the
root handler caplog listens on. These two tests passed alone and under `-n auto`
in isolation, and failed in the full suite, which is that trap exactly.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

import pytest

from aegis.daemon.protocol import hello
from aegis.webterm.relay import relay


class _Browser:
    """A browser that says hello, then stays open long enough to retry."""

    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self._frames: asyncio.Queue = asyncio.Queue()
        self._frames.put_nowait(hello("v1", 80, 24))

    async def receive(self):
        return await self._frames.get()

    async def send(self, data: bytes) -> None:
        self.sent.append(data)


class _Keep(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


@contextlib.contextmanager
def _captured():
    """Records from the relay's logger, whatever `propagate` is set to."""
    logger = logging.getLogger("aegis.webterm.relay")
    keep, prev = _Keep(), logger.level
    logger.addHandler(keep)
    logger.setLevel(logging.INFO)
    try:
        yield keep
    finally:
        logger.removeHandler(keep)
        logger.setLevel(prev)


async def _run_until_it_gives_up(connect):
    with pytest.raises((asyncio.TimeoutError, TimeoutError)):
        await asyncio.wait_for(relay(_Browser(), connect, delays=(0.01,)), 0.4)


async def test_the_first_failure_is_a_warning_and_names_the_reason():
    attempts = []

    async def connect():
        attempts.append(1)
        raise ConnectionError("no daemon socket at /tmp/x/daemon.sock")

    with _captured() as keep:
        await _run_until_it_gives_up(connect)

    assert attempts, "connect() was never called"
    records = [r for r in keep.records if "daemon" in r.getMessage()]
    assert records, "nothing was logged about the daemon at all"

    first = records[0]
    assert first.levelno >= logging.WARNING, (
        f"the first failure logged at {first.levelname}; uvicorn runs the root "
        "logger at warning, so anything lower reaches nobody"
    )
    assert "no daemon socket at /tmp/x/daemon.sock" in first.getMessage(), (
        "the message must carry the reason connect() gave, which names the socket"
    )


async def test_the_retries_after_it_stay_quiet():
    """A daemon restart is a normal reconnect; one line per attempt would make
    a routine restart look like an incident."""

    async def connect():
        raise ConnectionError("still down")

    with _captured() as keep:
        await _run_until_it_gives_up(connect)

    about_the_daemon = [r for r in keep.records if "daemon" in r.getMessage()]
    warnings = [r for r in about_the_daemon if r.levelno >= logging.WARNING]
    infos = [r for r in about_the_daemon if r.levelno < logging.WARNING]
    assert len(warnings) == 1, f"expected exactly one warning, got {len(warnings)}"
    assert infos, "the later attempts should still be logged, at info"
