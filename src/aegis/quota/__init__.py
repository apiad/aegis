"""Quota: how much of each subscription window is left, for the gauges.

``Quota`` holds one ``QuotaService`` per provider and runs one loop. Every
``TICK_S`` it asks each service to refresh, and the service's own floor decides
whether that really fetches (Claude every 180 s, OpenCode Go every 60 s). So one
loop carries both the fetch cadence and the re-evaluation of pace, which moves
with the clock. It then builds the wire snapshot and publishes it on the
``quota`` channel if it differs from the last one published; ``projected`` and
the times are rounded so that happens at most once a tick.

Python decides severity and the projection; the browser draws the tick and the
countdown from ``starts_at`` and ``resets_at`` (DESIGN.md, "Python decides, the
browser draws"). Reading the snapshot never fetches, so ``quota.read`` costs an
agent nothing against the vendor.

The cache under ``cache_dir()`` is shared by every aegis process on the
machine, the legacy tree's included: quota is an account property, and the
Claude endpoint 429s when several pollers ask (#41).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from .claude import PROVIDER as CLAUDE
from .core import (
    FAILURE_TEXT,
    QuotaProvider,
    QuotaService,
    QuotaState,
    cancel_and_wait,
    pace_severity,
    window_pace,
)
from .opencode import PROVIDER as OPENCODE_GO

log = logging.getLogger("aegis.quota")

PROVIDERS: tuple[QuotaProvider, ...] = (CLAUDE, OPENCODE_GO)
TICK_S = 60.0
Publish = Callable[[str, list[dict]], None]


def cache_dir() -> Path:
    """Where every aegis process on the machine shares its last reading."""
    if override := os.environ.get("AEGIS_QUOTA_CACHE"):
        return Path(override)
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "aegis" / "quota"


def _retry_at(state: QuotaState, wall: float) -> int | None:
    return round(wall + state.retry_in_s) if state.retry_in_s > 0 else None


def provider_wire(
    provider: QuotaProvider, state: QuotaState, *, now: datetime, wall: float
) -> dict | None:
    """One provider on the wire, or None when it has nothing to say: no
    credentials (a rail not in use), or no reading and no failure yet."""
    head = {"name": provider.name, "label": provider.label}
    note = FAILURE_TEXT.get(state.failure, state.failure)
    if state.snapshot is None:
        if not state.failure or state.failure == "no_credentials":
            return None
        return head | {
            "state": "failed",
            "note": note,
            "read_at": None,
            "retry_at": _retry_at(state, wall),
            "windows": [],
        }
    # A stale reading does not get to speak about pace: its percent is frozen
    # while the clock runs on, so the projection would fall on its own.
    stale = bool(state.failure)
    windows = []
    for kind, label in provider.bar_windows:
        w = state.snapshot.window(kind)
        if w is None:
            continue
        span = None if stale else provider.window_spans.get(kind)
        projected = window_pace(w, span, now=now)
        resets = None if w.resets_at is None else w.resets_at.timestamp()
        starts = None
        if projected is not None and resets is not None and span:
            starts = round(resets - span)
        windows.append(
            {
                "kind": kind,
                "label": label,
                "percent": w.percent,
                "severity": "normal" if stale else pace_severity(w, span, now=now),
                "projected": None if projected is None else round(projected),
                "starts_at": starts,
                "resets_at": None if resets is None else round(resets),
            }
        )
    return head | {
        "state": "stale" if stale else "ok",
        "note": note if stale else "",
        "read_at": round(wall - state.age_s),
        "retry_at": _retry_at(state, wall),
        "windows": windows,
    }


class Quota:
    def __init__(
        self,
        publish: Publish,
        *,
        providers: tuple[QuotaProvider, ...] = PROVIDERS,
        cache: Path | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
    ) -> None:
        self._publish = publish
        self._providers = providers
        self._wall = wall
        root = cache_dir() if cache is None else cache
        self.services = {
            p.name: QuotaService(
                fetch=p.fetch,
                token_reader=p.read_token,
                clock=clock,
                poll_s=p.poll_s,
                cache=root / f"{p.name}.json",
                wall=wall,
            )
            for p in providers
        }
        self._last: dict | None = None
        self._task: asyncio.Task | None = None
        self._nudges: set[asyncio.Task] = set()

    def snapshot(self) -> dict:
        wall = self._wall()
        now = datetime.fromtimestamp(wall, timezone.utc)
        providers = []
        for p in self._providers:
            wire = provider_wire(p, self.services[p.name].current(), now=now, wall=wall)
            if wire is not None:
                providers.append(wire)
        return {"providers": providers}

    def check(self) -> None:
        snap = self.snapshot()
        if snap != self._last:
            self._last = snap
            self._publish("quota", [{"set": snap}])

    async def tick(self) -> None:
        for p in self._providers:
            await self.services[p.name].refresh()
        self.check()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:  # a tick must never end the loop
                log.exception("quota tick failed")
            await asyncio.sleep(TICK_S)

    async def stop(self) -> None:
        task, self._task = self._task, None
        for t in list(self._nudges):
            await cancel_and_wait(t)
        await cancel_and_wait(task)

    def turn_ended(self) -> None:
        """A Claude turn just spent quota: refresh Claude before its cadence
        would, but no sooner than its turn floor after the last fetch."""
        if self._task is None:
            return
        for p in self._providers:
            if p.name == CLAUDE.name:
                t = asyncio.create_task(self._nudge(p))
                self._nudges.add(t)
                t.add_done_callback(self._nudges.discard)

    async def _nudge(self, p: QuotaProvider) -> None:
        await self.services[p.name].refresh(min_interval=p.turn_floor_s)
        self.check()
