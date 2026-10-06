"""Polls the three probes on three cadences and caches the answer.

`state` is a plain attribute read, so the 1-second UI tick never touches the
network — the same division `QuotaService` draws (`aegis/usage/quota.py`).
This service holds one asyncio task and no thread: the probes are already
async, where the quota endpoint is blocking stdlib code that needed one.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, replace
from typing import Any, NamedTuple

from aegis.net.probe import Reach, Throughput, Trace


class Probes(NamedTuple):
    """The three callables, in one object so a test injects one thing."""

    reach: Any
    trace: Any
    throughput: Any


def default_probes() -> Probes:
    from aegis.net import probe

    return Probes(
        reach=lambda anchors, timeout: probe.reach(anchors, timeout),
        trace=lambda timeout: probe.trace(timeout=timeout),
        throughput=lambda nbytes, timeout: probe.throughput(
            nbytes=nbytes, timeout=timeout
        ),
    )


@dataclass(frozen=True)
class NetState:
    """The last reading of each fact, each with its own timestamp.

    Separate stamps because the renderer ages them independently: a
    throughput figure from four minutes ago is worth showing with its age,
    while an RTT from four minutes ago is not an RTT.
    """

    reach: Reach | None = None
    reach_at: float = 0.0
    trace: Trace | None = None
    trace_at: float = 0.0
    speed: Throughput | None = None
    speed_at: float = 0.0
    # Non-empty when the LAST lookup attempt failed while an earlier address
    # was kept. The address is still the best guess and stays on screen; this
    # is what lets the renderer date it instead of presenting it as current.
    trace_error: str = ""

    @property
    def sampled(self) -> bool:
        """Whether anything has been measured yet.

        The renderer shows an ellipsis rather than `0ms` until this is true:
        a zero claims a measurement.
        """
        return self.reach is not None


class NetService:
    def __init__(self, cfg, *, probes: Probes | None = None, clock=time.monotonic):
        self._cfg = cfg
        self._probes = probes or default_probes()
        self._clock = clock
        self._state = NetState()
        self._task: asyncio.Task | None = None
        # None until the first reading, so the very first success is not
        # mistaken for egress "coming back" and does not force a second trace
        # on top of the one the first refresh already takes.
        self._was_ok: bool | None = None
        # `/net` awaits refresh(force_speed=True) for up to speed_timeout while
        # `_loop` keeps sampling on its own cadence. refresh() reads _state,
        # awaits, then writes, so unserialised the slow caller stamps its
        # pre-outage snapshot over the reading that saw the outage — and the
        # sidebar goes green on a dead link.
        self._lock = asyncio.Lock()

    @property
    def state(self) -> NetState:
        return self._state

    @property
    def started(self) -> bool:
        return self._task is not None

    def start(self) -> None:
        """Begin polling. Idempotent — safe to call from every UI tick."""
        if self._task is not None or not self._cfg.enabled:
            return
        self._task = asyncio.create_task(self._loop())

    def cancel(self) -> None:
        """Stop the loop without awaiting, for a synchronous teardown.

        `on_unmount` is not a coroutine, and Textual dispatches Unmount on
        every shutdown path where `action_quit` covers only one — so a service
        released only in `action_quit` leaks a probe loop per app that exits
        any other way, and the loop's bound method keeps the whole app alive.
        """
        task, self._task = self._task, None
        if task is not None:
            task.cancel()

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    async def _loop(self) -> None:
        while True:
            await self.refresh()
            await asyncio.sleep(self._cfg.interval)

    async def refresh(self, *, force_speed: bool = False) -> None:
        """Take the readings that are due. Never raises.

        Serialised: see `self._lock`. `/net` and the background loop both call
        this, and a read-await-write body cannot be interleaved safely.
        """
        async with self._lock:
            await self._refresh(force_speed=force_speed)

    async def _refresh(self, *, force_speed: bool) -> None:
        now = self._clock()
        try:
            found = await self._probes.reach(self._cfg.anchors, self._cfg.timeout)
        except Exception as exc:  # noqa: BLE001 — a raise must not reach the tick
            found = Reach(ok=False, error=type(exc).__name__)
        was, self._was_ok = self._was_ok, found.ok
        state = replace(self._state, reach=found, reach_at=now)

        if found.ok:
            # Its own cadence, plus the moment egress comes back: that
            # transition is when the address has actually had a chance to
            # change, because it is when you joined another network.
            came_back = was is False
            due = (
                state.trace is None or now - state.trace_at >= self._cfg.trace_interval
            )
            if due or came_back:
                state = self._with_trace(state, await self._trace(), now)

            want_speed = force_speed or (
                self._cfg.speed_interval > 0
                and (
                    state.speed is None
                    or now - state.speed_at >= self._cfg.speed_interval
                )
            )
            if want_speed:
                state = replace(state, speed=await self._speed(), speed_at=now)

        self._state = state

    @staticmethod
    def _with_trace(state: NetState, found: Trace, now: float) -> NetState:
        """Keep the last known address when a lookup fails.

        Losing the lookup says nothing about what the IP was, and the row it
        would blank is the one that tells you which network you fell onto. A
        failure is recorded only when there is nothing to lose.
        """
        if found.ok:
            return replace(state, trace=found, trace_at=now, trace_error="")
        if state.trace is None:
            return replace(state, trace=found, trace_at=now, trace_error=found.error)
        # Keep the address and record that it was not re-confirmed, so the row
        # can show its age rather than pass a stale address off as current.
        return replace(state, trace_error=found.error or "lookup failed")

    async def _trace(self) -> Trace:
        try:
            return await self._probes.trace(self._cfg.timeout)
        except Exception as exc:  # noqa: BLE001
            return Trace(ok=False, error=type(exc).__name__)

    async def _speed(self) -> Throughput:
        try:
            return await self._probes.throughput(
                self._cfg.speed_bytes, self._cfg.speed_timeout
            )
        except Exception as exc:  # noqa: BLE001
            return Throughput(
                ok=False, asked=self._cfg.speed_bytes, error=type(exc).__name__
            )
