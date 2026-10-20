"""Named state channels: a snapshot on subscribe, then numbered patches.

Each subscription numbers its own patches from 1, after the snapshot's 0. A
client that sees a gap, or reconnects, resubscribes and takes a fresh snapshot;
that one rule covers dropped frames, a sleeping laptop and a server restart. A
subscribe may carry ``since``, the revision the client already holds; a channel
that keeps revisions (a transcript) answers with only what changed after it,
and any other channel ignores it.
Adding a subsystem adds channels, never a protocol field.

A subscribe may also carry ``merge: true`` on a channel whose rows have a key
(``MERGE_KEYS``). That subscriber's patches then turn each ``upsert`` of a row
it already holds into ``{"merge": {key, ...}}`` with only the fields that
changed, and drop one that changed nothing. A working session changes one or
two of its 40-odd fields per patch, so whole rows were most of an idle
client's traffic (#525). The rows a subscriber holds are kept per
subscription, from its own snapshot on, so a merge is exact for it whatever
the others were sent. A subscriber that did not ask (an older client or link)
keeps getting whole rows.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from .ops import OpError

Send = Callable[[dict], None]
Snapshot = Callable[[], Any]
Resolve = Callable[[str, int | None], Snapshot | None]

# The channels a subscriber may take merges on, and the field that keys a row.
MERGE_KEYS = {"sessions": "log_id"}


class Sub:
    def __init__(self, channel: str, send: Send, merge: bool = False) -> None:
        self.channel = channel
        self.send = send
        self.seq = 0
        # The rows this subscriber holds, by key; None when it takes whole rows.
        self.rows: dict[Any, dict] | None = {} if merge else None


def _merged(key: str, rows: dict[Any, dict], ops: list[dict]) -> list[dict]:
    """``ops`` as one merge subscriber is sent them, against ``rows``, the rows
    it holds, which this updates."""
    out = []
    for op in ops:
        row = op.get("upsert")
        if row is None:
            if "remove" in op:
                rows.pop(op["remove"], None)
            out.append(op)
            continue
        k = row[key]
        held = rows.get(k)
        rows[k] = row
        # A field gone from the row has no merge to say so: send it whole.
        if held is None or held.keys() - row.keys():
            out.append(op)
            continue
        changed = {f: v for f, v in row.items() if f not in held or held[f] != v}
        if changed:
            out.append({"merge": {key: k, **changed}})
    return out


class Channels:
    def __init__(self, resolve: Resolve) -> None:
        self._resolve = resolve
        self._subs: dict[str, list[Sub]] = {}

    def subscribe(
        self, channel: str, send: Send, since: int | None = None, merge: bool = False
    ) -> Sub:
        snapshot = self._resolve(channel, since)
        if snapshot is None:
            raise OpError("unknown_channel", f"no channel named {channel!r}")
        sub = Sub(channel, send, merge and channel in MERGE_KEYS)
        self._subs.setdefault(channel, []).append(sub)
        data = snapshot()
        if sub.rows is not None:
            key = MERGE_KEYS[channel]
            sub.rows = {row[key]: row for row in data}
        send({"t": "snapshot", "channel": channel, "seq": 0, "data": data})
        return sub

    def unsubscribe(self, sub: Sub) -> None:
        subs = self._subs.get(sub.channel, [])
        if sub in subs:
            subs.remove(sub)

    def publish(self, channel: str, ops: list[dict]) -> None:
        if not ops:
            return
        for sub in list(self._subs.get(channel, ())):
            out = (
                ops if sub.rows is None else _merged(MERGE_KEYS[channel], sub.rows, ops)
            )
            if not out:
                continue
            sub.seq += 1
            sub.send({"t": "patch", "channel": channel, "seq": sub.seq, "ops": out})

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
