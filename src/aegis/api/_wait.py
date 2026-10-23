"""Waiting on a condition that aegis's channels announce.

Each waiter subscribes to the channels whose messages mean "look again", and
reads the live state when one arrives, with a one-second fallback for a
publish a throttle merged away (the sessions channel batches its cards).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..server import Server


async def until(
    server: Server,
    done: Callable[[], bool],
    channels: tuple[str, ...],
    timeout: float | None,
) -> None:
    """Return once ``done()`` is true; TimeoutError after ``timeout`` seconds."""
    app = server.app
    changed = asyncio.Event()
    subs = [app.channels.subscribe(c, lambda _m: changed.set()) for c in channels]
    try:
        async with asyncio.timeout(timeout):
            while not done():
                changed.clear()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(changed.wait(), 1.0)
    finally:
        for sub in subs:
            app.channels.unsubscribe(sub)
