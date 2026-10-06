"""Named state channels: a snapshot on subscribe, then numbered patches.

Each subscription numbers its own patches from 1, after the snapshot's 0. A
client that sees a gap, or reconnects, resubscribes and takes a fresh snapshot;
that one rule covers dropped frames, a sleeping laptop and a server restart.
Adding a subsystem adds channels, never a protocol field.
"""

from __future__ import annotations

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
