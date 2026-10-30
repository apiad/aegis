"""Polls the three probes on three cadences and caches the answer.

`state` is a plain attribute read, so publishing never touches the network.
One asyncio task and no thread: the probes are already async.

Two rules aegis 2 adds to the legacy service. The config is read on every
pass, so an edit to ``network:`` applies without a restart. And nothing is
probed while nobody watches the ``host`` channel, the rule host.py follows:
the loop idles on a one-second poll and refreshes the moment a browser
subscribes, so a reading arrives without waiting a whole interval.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, NamedTuple

from .config import NetConfig
from .probe import Reach, Throughput, Trace

IDLE_POLL_S = 1.0


class Probes(NamedTuple):
    """The three callables, in one object so a test injects one thing."""

    reach: Any
    trace: Any
    throughput: Any


def default_probes() -> Probes:
    """The real probes, unless ``AEGIS_NET_PROBES`` says otherwise.

    The test suite's ``aegis serve`` runs in a subprocess no monkeypatch
    reaches, and these probes need no credentials, so without a switch every
    browser test would open sockets to the anchors and download a megabyte,
    on CI too. ``offline`` fails every probe at once; ``sample`` answers with
    fixed readings from the documentation address range, for the tests that
    draw the rows."""
    import os

    from . import probe

    mode = os.environ.get("AEGIS_NET_PROBES", "")
    if mode == "offline":
        return _canned(ok=False)
    if mode == "sample":
        return _canned(ok=True)
    return Probes(
        reach=lambda anchors, timeout: probe.reach(anchors, timeout),
        trace=lambda timeout, proxy=None: probe.trace(timeout=timeout, proxy=proxy),
        throughput=lambda nbytes, timeout: probe.throughput(
            nbytes=nbytes, timeout=timeout
        ),
    )


def _canned(*, ok: bool) -> Probes:
    async def reach(anchors, timeout):
        if not ok:
            return Reach(ok=False, error="offline")
        return Reach(ok=True, rtt_ms=24.0, anchor="1.1.1.1:443")

    async def trace(timeout, proxy=None):
        if not ok:
            return Trace(ok=False, error="offline")
        if proxy is not None:
            return Trace(ok=True, ip="203.0.113.9", colo="HEL", loc="FI")
        return Trace(ok=True, ip="203.0.113.7", colo="MIA", loc="US")

    async def throughput(nbytes, timeout):
        if not ok:
            return Throughput(ok=False, asked=nbytes, error="offline")
        await asyncio.sleep(0.5)  # long enough for a test to see "measuring"
        return Throughput(
            ok=True,
            bytes_per_s=nbytes / 0.96,
            received=nbytes,
            asked=nbytes,
            elapsed_s=0.96,
        )

    return Probes(reach=reach, trace=trace, throughput=throughput)


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
    # The address through the exit (exit.py), on its own stamp: this machine's
    # route may be down while the exit works, which is why it is there.
    via: Trace | None = None
    via_at: float = 0.0
    via_name: str = ""
    # Why the exit carries nothing now; empty when it does.
    via_down: str = ""

    @property
    def sampled(self) -> bool:
        """Whether anything has been measured yet.

        The renderer shows an ellipsis rather than `0ms` until this is true:
        a zero claims a measurement.
        """
        return self.reach is not None


class NetService:
    def __init__(
        self,
        cfg: Callable[[], NetConfig],
        *,
        probes: Probes | None = None,
        watched: Callable[[], bool] = lambda: True,
        on_change: Callable[[], None] = lambda: None,
        clock=time.monotonic,
        wall=time.time,
        exit_now: Callable[[], Any] = lambda: None,
    ):
        self._cfg = cfg
        # The exit in force, with ``name``, ``url`` and ``down()``, or None.
        self._exit_now = exit_now
        self._probes = probes or default_probes()
        self._watched = watched
        self._on_change = on_change
        self._clock = clock
        self._wall = wall
        self._state = NetState()
        self._task: asyncio.Task | None = None
        self.measuring = False
        # None until the first reading, so the very first success is not
        # mistaken for egress "coming back" and does not force a second trace
        # on top of the one the first refresh already takes.
        self._was_ok: bool | None = None
        # `net.measure` awaits refresh(force_speed=True) for up to
        # speed_timeout while `_loop` keeps sampling on its own cadence.
        # refresh() reads _state, awaits, then writes, so unserialised the slow
        # caller stamps its pre-outage snapshot over the reading that saw the
        # outage, and the row goes green on a dead link.
        self._lock = asyncio.Lock()

    @property
    def state(self) -> NetState:
        return self._state

    @property
    def started(self) -> bool:
        return self._task is not None

    def start(self) -> None:
        """Begin polling. Idempotent."""
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    def cancel(self) -> None:
        """Stop the loop without awaiting, for a synchronous teardown."""
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
            cfg = self._cfg()
            if cfg.enabled and self._watched():
                await self.refresh()
                await asyncio.sleep(cfg.interval)
            else:
                await asyncio.sleep(IDLE_POLL_S)

    async def measure(self) -> None:
        """A speed reading now, whatever the cadence says."""
        self.measuring = True
        self._on_change()
        try:
            await self.refresh(force_speed=True)
        finally:
            self.measuring = False
            self._on_change()

    async def refresh(self, *, force_speed: bool = False) -> None:
        """Take the readings that are due. Never raises.

        Serialised: see `self._lock`. `/net` and the background loop both call
        this, and a read-await-write body cannot be interleaved safely.
        """
        async with self._lock:
            await self._refresh(force_speed=force_speed)
        self._on_change()

    async def _refresh(self, *, force_speed: bool) -> None:
        cfg = self._cfg()
        now = self._clock()
        try:
            found = await self._probes.reach(cfg.anchors, cfg.timeout)
        except Exception as exc:  # noqa: BLE001 — a raise must not reach the tick
            found = Reach(ok=False, error=type(exc).__name__)
        was, self._was_ok = self._was_ok, found.ok
        state = replace(self._state, reach=found, reach_at=now)

        if found.ok:
            # Its own cadence, plus the moment egress comes back: that
            # transition is when the address has actually had a chance to
            # change, because it is when you joined another network.
            came_back = was is False
            due = state.trace is None or now - state.trace_at >= cfg.trace_interval
            if due or came_back:
                state = self._with_trace(state, await self._trace(cfg), now)

            want_speed = force_speed or (
                cfg.speed_interval > 0
                and (state.speed is None or now - state.speed_at >= cfg.speed_interval)
            )
            if want_speed:
                state = replace(state, speed=await self._speed(cfg), speed_at=now)

        self._state = await self._with_via(state, cfg, now)

    async def _with_via(self, state: NetState, cfg: NetConfig, now: float) -> NetState:
        found = self._exit_now()
        if found is None:
            return replace(state, via=None, via_name="", via_down="")
        down = found.down() or ""
        changed = found.name != state.via_name or down != state.via_down
        state = replace(state, via_name=found.name, via_down=down)
        due = state.via is None or now - state.via_at >= cfg.trace_interval
        if down or not (due or changed):
            return state
        try:
            trace = await self._probes.trace(cfg.timeout, found.url)
        except Exception as exc:  # noqa: BLE001
            trace = Trace(ok=False, error=type(exc).__name__)
        return replace(state, via=trace, via_at=now)

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

    async def _trace(self, cfg: NetConfig) -> Trace:
        try:
            return await self._probes.trace(cfg.timeout)
        except Exception as exc:  # noqa: BLE001
            return Trace(ok=False, error=type(exc).__name__)

    async def _speed(self, cfg: NetConfig) -> Throughput:
        try:
            return await self._probes.throughput(cfg.speed_bytes, cfg.speed_timeout)
        except Exception as exc:  # noqa: BLE001
            return Throughput(ok=False, asked=cfg.speed_bytes, error=type(exc).__name__)

    def wire(self) -> dict | None:
        """The `net` field of the host snapshot; None before the first reading.

        Times are epoch seconds, converted from the monotonic stamps the
        cadences run on, so the browser prints ages with its own clock."""
        s = self._state
        if s.reach is None:
            return None
        now, wall = self._clock(), self._wall()

        def at(stamp: float) -> int:
            return round(wall - (now - stamp))

        r, tr, sp = s.reach, s.trace, s.speed
        return {
            "ok": r.ok,
            "rtt_ms": round(r.rtt_ms) if r.ok else None,
            "anchor": r.anchor,
            "error": r.error,
            "at": at(s.reach_at),
            "ip": tr.ip if tr else "",
            "colo": tr.colo if tr else "",
            "loc": tr.loc if tr else "",
            "ip_at": at(s.trace_at) if tr and tr.ok else None,
            "ip_error": s.trace_error,
            "via": None
            if not s.via_name
            else {
                "name": s.via_name,
                "down": s.via_down,
                "ip": s.via.ip if s.via else "",
                "colo": s.via.colo if s.via else "",
                "loc": s.via.loc if s.via else "",
                "error": s.via.error if s.via else "",
                "at": at(s.via_at) if s.via else None,
            },
            "speed": None
            if sp is None
            else {
                "bps": round(sp.bytes_per_s) if sp.complete else None,
                "received": sp.received,
                "asked": sp.asked,
                "elapsed_s": round(sp.elapsed_s, 2),
                "complete": sp.complete,
                "error": sp.error,
                "at": at(s.speed_at),
            },
            "measuring": self.measuring,
        }
