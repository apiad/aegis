"""Named state channels: a snapshot on subscribe, then numbered patches.

Each subscription numbers its own patches from 1, after the snapshot's 0. A
client that sees a gap, or reconnects, resubscribes and takes a fresh snapshot;
that one rule covers dropped frames, a sleeping laptop and a server restart.
Adding a subsystem adds channels, never a protocol field.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from .ops import OpError

Send = Callable[[dict], None]
Snapshot = Callable[[], Any]


class Sub:
    def __init__(self, channel: str, send: Send) -> None:
        self.channel = channel
        self.send = send
        self.seq = 0


class Channels:
    def __init__(self, resolve: Callable[[str], Snapshot | None]) -> None:
        self._resolve = resolve
        self._subs: dict[str, list[Sub]] = {}

    def subscribe(self, channel: str, send: Send) -> Sub:
        snapshot = self._resolve(channel)
        if snapshot is None:
            raise OpError("unknown_channel", f"no channel named {channel!r}")
        sub = Sub(channel, send)
        self._subs.setdefault(channel, []).append(sub)
        send({"t": "snapshot", "channel": channel, "seq": 0, "data": snapshot()})
        return sub

    def unsubscribe(self, sub: Sub) -> None:
        subs = self._subs.get(sub.channel, [])
        if sub in subs:
            subs.remove(sub)

    def publish(self, channel: str, ops: list[dict]) -> None:
        if not ops:
            return
        for sub in list(self._subs.get(channel, ())):
            sub.seq += 1
            sub.send({"t": "patch", "channel": channel, "seq": sub.seq, "ops": ops})

    def subscribers(self, channel: str) -> int:
        return len(self._subs.get(channel, ()))


class Throttle:
    """One channel's ops, merged by key and published at most once per ``every``.

    The first ops after a quiet interval go out at once; ops arriving inside the
    interval wait for its end and go out as one patch, the last op per key
    winning. An op the key function marks urgent goes out at once, with
    whatever is pending: a change a person acts on must not wait. A working session changes its card on nearly every Claude line, so
    without this the ``sessions`` channel sent up to four patches a second per
    working session, and every browser redrew on each (#158). With it the rate
    is bounded whatever the number of sessions. Without a running loop (a sync
    caller) ops go out at once.
    """

    def __init__(
        self,
        publish: Callable[[list[dict]], None],
        key: Callable[[dict], tuple[Any, bool]],  # op -> (its key, urgent)
        every: float,
    ) -> None:
        self._publish = publish
        self._key = key
        self._every = every
        self._pending: dict[Any, dict] = {}
        self._last = float("-inf")
        self._timer: asyncio.TimerHandle | None = None

    def add(self, ops: list[dict]) -> None:
        urgent = False
        for op in ops:
            k, now = self._key(op)
            urgent |= now
            self._pending.pop(k, None)  # keep arrival order: the latest goes last
            self._pending[k] = op
        if urgent:
            self._flush()
            return
        if self._timer is not None or not self._pending:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._flush()
            return
        wait = self._last + self._every - loop.time()
        if wait <= 0:
            self._flush()
        else:
            self._timer = loop.call_later(wait, self._flush)

    def _flush(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
        self._timer = None
        ops = list(self._pending.values())
        self._pending.clear()
        try:
            self._last = asyncio.get_running_loop().time()
        except RuntimeError:
            pass
        if ops:
            self._publish(ops)
