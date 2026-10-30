"""Subscription-quota core, shared by every provider.

A provider module (``claude``, ``opencode``) knows one vendor's credentials,
endpoint and payload shape, and produces a ``QuotaSnapshot``. Polling,
staleness, severity and pace live here and know nothing about any vendor.
Copied from the legacy tree's ``aegis/usage/quota.py`` without its Rich
renderers; ``aegis.quota.Quota`` turns readings into the wire snapshot.

``QuotaProvider`` is the seam: a third vendor is a new module plus an entry in
``aegis.quota.PROVIDERS``, not a change here.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Mapping

POLL_S = 60.0  # background cadence, unless the provider sets its own
BACKOFF_S = 300.0  # hands off the endpoint after it says 429

# Used only when the API omits its own `severity` for a window.
WARNING_AT = 80.0
CRITICAL_AT = 90.0

# A window warns only when it is over pace: its *projected* spend at reset is
# above 100%, so the quota runs out before the period does. Under pace there is
# nothing to change, however much is gone, until level reaches CRITICAL_AT.
PACE_WARN_AT = 100.0
# Over pace with less than this spent is not yet a warning: early in a window
# the projection rests on a thin slice, and there is room left to slow down.
PACE_WARN_MIN = 50.0
# How much of a window has to pass before its own rate weighs as much as the
# rate of the windows before it. Spend comes in bursts and one day's rate says
# little about the next, so a rate from a thin slice is mostly noise. Backtested
# on 73 five-hour windows the error is flat from 0.3 to 1.2. Three weeks that all
# ended near the limit cannot place it, and 0.5 also holds when they are scaled
# to half or one and a half times their spend.
PACE_PRIOR_WEIGHT = 0.5
# What a window usually spends when no window of its kind has finished yet: a
# window that spends exactly its quota by the reset.
PACE_USUAL_DEFAULT = 100.0
# How many finished windows make up "usually": short ones vary more and pass
# quickly, and four weeks or months already reach far back.
PACE_HISTORY_SHORT = 20
PACE_HISTORY_LONG = 4
# A reset time further out than the span means the clock is off or our span for
# that kind is wrong. This much is ordinary clock skew; past it we stop
# projecting rather than invent a number from a bad span.
PACE_SKEW_GRACE_S = 60.0


def boot_clock() -> float:
    """Seconds on a clock that keeps counting while the machine sleeps.

    ``CLOCK_MONOTONIC`` stops during suspend on Linux, so a reading taken
    before a laptop slept for eight hours read as seconds old after it woke:
    shown as fresh, and inside the floor, so never refetched. ``CLOCK_BOOTTIME``
    counts the sleep. Every reading's ``fetched_at`` and every service's age
    and floor use this one clock; mixing it with ``time.monotonic`` would put
    the total time asleep into every age.
    """
    if _BOOTTIME is not None:
        return time.clock_gettime(_BOOTTIME)
    return time.monotonic()


_BOOTTIME = getattr(time, "CLOCK_BOOTTIME", None)


class QuotaError(Exception):
    """A fetch failed.

    ``kind`` is ``unauthorized``, ``rate_limited`` or ``unreachable``.
    """

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


@dataclass(frozen=True)
class QuotaWindow:
    kind: str  # "session", "weekly_all", "weekly_opus", ...
    percent: float
    severity: str  # "normal" | "warning" | "critical"
    resets_at: datetime | None
    is_active: bool


@dataclass(frozen=True)
class QuotaSnapshot:
    windows: tuple[QuotaWindow, ...]
    fetched_at: float  # monotonic

    def window(self, kind: str) -> QuotaWindow | None:
        for w in self.windows:
            if w.kind == kind:
                return w
        return None


@dataclass(frozen=True)
class QuotaProvider:
    """One vendor's quota, as data the generic machinery can act on.

    ``harness`` names the aegis driver whose panes spend this quota — used to
    route a turn-end refresh to the provider whose number just moved. It does
    *not* gate visibility: quota is an account property, so every provider we
    hold credentials for is shown whether or not one of its agents is open.
    That is the point — it is what tells you which rail to launch on.
    """

    name: str  # "claude" | "opencode-go"
    label: str  # bar prefix when >1 is shown
    harness: str  # matches an Agent's harness
    bar_windows: tuple[tuple[str, str], ...]  # (kind, label), display order
    fetch: Callable[..., "QuotaSnapshot"]
    read_token: Callable[..., str | None]
    # How long each window lasts, in seconds, keyed by kind. Neither payload
    # carries a window length or a start time, only `resets_at`, so this is the
    # other half of what pace needs. A kind that is absent here gets no
    # projection and keeps its level-only colour, which is what makes a new
    # vendor window safe to meet.
    window_spans: Mapping[str, float] = field(default_factory=dict)
    # Background cadence, and the floor a turn-end refresh honours. Per
    # provider because Claude's endpoint 429s under polling that OpenCode's
    # takes without complaint.
    poll_s: float = POLL_S
    turn_floor_s: float = 10.0
    # What identifies the account the reading is of, read locally, or None. Only
    # its hash goes on the wire, so two linked servers on one account can show
    # its gauges once (links.py) without either learning the other's id.
    account: Callable[[], str | None] = lambda: None


def _severity(percent: float, given) -> str:
    if given in ("normal", "warning", "critical"):
        return given
    if percent >= CRITICAL_AT:
        return "critical"
    if percent >= WARNING_AT:
        return "warning"
    return "normal"


async def cancel_and_wait(task: asyncio.Task | None) -> None:
    """Cancel a background task and wait for it to finish.

    The task's own CancelledError is expected and swallowed. One aimed at our
    caller, which arrives while we wait, is re-raised: the legacy ``stop()``
    swallowed both, so a shutdown that cancelled it carried on as if nothing
    had happened (#72). Any other error from a dying task is swallowed, because
    it must not break a shutdown.
    """
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        current = asyncio.current_task()
        if current is not None and current.cancelling():
            raise
    except Exception:  # noqa: BLE001
        pass


def window_pace(
    window: QuotaWindow,
    span_s,
    *,
    now: datetime,
    usual: float = PACE_USUAL_DEFAULT,
) -> float | None:
    """Where this window's spend lands at reset, as a percent of the quota.

    What is spent is known, so only the time left is projected, at the posterior
    mean of a rate with a Gamma prior: ``(b * usual + percent) / (b + elapsed)``,
    where ``usual`` is what a finished window spends. That is the window's own
    rate and the usual one averaged with weight ``elapsed / (b + elapsed)``, so a
    new window reads ``usual``, a finished one reads what it spent, and nothing
    between them divides by a thin slice or moves in a step.

    ``None`` means we decline to answer, and every caller then falls back to the
    level verdict. That happens when the window carries no reset time, when we
    hold no span for its kind, and when the reset time is further out than the
    span says it can be.
    """
    if window.resets_at is None or not span_s or span_s <= 0:
        return None
    remaining = (window.resets_at - now).total_seconds()
    if remaining > span_s + PACE_SKEW_GRACE_S:
        return None
    elapsed = (span_s - max(0.0, min(remaining, span_s))) / span_s
    rate = (PACE_PRIOR_WEIGHT * usual + window.percent) / (PACE_PRIOR_WEIGHT + elapsed)
    return window.percent + (1 - elapsed) * rate


def pace_severity(
    window: QuotaWindow,
    span_s,
    *,
    now: datetime,
    usual: float = PACE_USUAL_DEFAULT,
) -> str:
    """The window's colour, from its pace when it has one.

    Critical is level: CRITICAL_AT spent, or the vendor's own critical, so a
    window at 97% with five minutes left stays red although it projects to
    97%. Warning is pace alone: over PACE_WARN_AT at reset with PACE_WARN_MIN
    already spent. A window under pace is normal below critical, which drops
    the level warning and a vendor's warning with it: spend that the period
    absorbs needs no one to slow down. A window with no projection keeps its
    level verdict.
    """
    projected = window_pace(window, span_s, now=now, usual=usual)
    if projected is None:
        return window.severity
    if window.severity == "critical" or window.percent >= CRITICAL_AT:
        return "critical"
    if projected > PACE_WARN_AT and window.percent >= PACE_WARN_MIN:
        return "warning"
    return "normal"


def _timestamp(raw) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass(frozen=True)
class QuotaState:
    """What the bar should show right now.

    ``snapshot`` and no ``failure`` is a fresh reading; ``snapshot`` with a
    ``failure`` is the last good reading while fetches are failing; no
    ``snapshot`` means there is nothing trustworthy to show and ``failure``
    says why.
    """

    snapshot: QuotaSnapshot | None = None
    age_s: float = 0.0
    failure: str = ""  # "" | "no_credentials" | "unauthorized" | "unreachable"
    retry_in_s: float = 0.0  # >0 while a 429 backoff runs


class QuotaService:
    """Polls the quota endpoint on a cadence and caches the answer.

    ``current()`` is synchronous, so reading the gauges never touches the
    network. Fetches run in a worker thread; the endpoint is blocking stdlib
    code and must not stall the event loop.

    The last good reading is never dropped. While fetches fail it stays on
    screen as stale, with its real age, because an old number is more use than
    a blank row (#41).

    ``cache`` is a JSON file shared by every aegis process on the machine,
    including a legacy-tree aegis still running. Each one adopts a
    reading another fetched inside the floor instead of asking again, and a 429
    any of them sees backs off all of them. Quota is an account property, so
    one reading per account is the honest number; N pollers were what starved
    Claude's endpoint into 429s in the first place. Times in the file are wall
    clock, converted to this process's monotonic clock on load.
    """

    def __init__(
        self,
        *,
        fetch,
        token_reader,
        clock=boot_clock,
        poll_s: float = POLL_S,
        cache: Path | None = None,
        wall=time.time,
    ) -> None:
        self._clock = clock
        self._wall = wall
        self._fetch = fetch
        self._read_token = token_reader
        self._poll_s = poll_s
        self._cache = cache
        self._snapshot: QuotaSnapshot | None = None
        self._failure = ""
        self._last_attempt = 0.0
        self._backoff_until = 0.0
        self._task = None
        # Readings come from the sessions' own stream too (observe). Once one
        # has, the endpoint refusing our credential says nothing about the
        # account: a setup-token cannot read it (claude/login.py).
        self._streamed = False

    @property
    def started(self) -> bool:
        return self._task is not None

    def start(self) -> None:
        """Begin polling. Idempotent."""
        if self._task is not None:
            return
        import asyncio

        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        task, self._task = self._task, None
        await cancel_and_wait(task)

        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass

    async def _loop(self) -> None:
        import asyncio

        while True:
            await self.refresh()
            await asyncio.sleep(self._poll_s)

    async def refresh(
        self, *, force: bool = False, min_interval: float | None = None
    ) -> None:
        """Fetch unless we, or another process, fetched recently.

        ``min_interval`` overrides the default floor — the turn-end trigger
        passes a shorter one so a finished turn updates the number promptly
        without letting a burst of short turns hammer an undocumented endpoint.

        ``force`` skips the floor but never the 429 backoff: once the endpoint
        has asked us to stop, an explicit refresh must not override it.
        """
        now = self._clock()
        if now < self._backoff_until:
            return
        floor = self._poll_s if min_interval is None else min_interval
        if not force and self._last_attempt and now - self._last_attempt < floor:
            return
        self._last_attempt = now

        token = self._read_token()
        if not token:
            if not self._streamed:
                self._snapshot = None
                self._failure = "no_credentials"
            return

        self._load_cache(now)
        if now < self._backoff_until:
            return
        if (
            not force
            and self._snapshot is not None
            and not self._failure
            and now - self._snapshot.fetched_at < floor
        ):
            return

        try:
            import asyncio

            snapshot = await asyncio.to_thread(self._fetch, token)
        except QuotaError as exc:
            if not (self._streamed and exc.kind == "unauthorized"):
                self._note_failure(exc.kind, now)
            return
        except Exception:  # noqa: BLE001 — a fetch must never break the caller
            self._note_failure("unreachable", now)
            return
        self._snapshot = snapshot
        self._failure = ""
        self._backoff_until = 0.0
        self._save_cache()

    def observe(self, snapshot: QuotaSnapshot) -> None:
        """A reading a session's stream carried: it is as good as a fetch,
        and newer than any cached one."""
        self._streamed = True
        self._snapshot = snapshot
        self._failure = ""
        self._save_cache()

    def _note_failure(self, kind: str, now: float) -> None:
        self._failure = kind
        if kind == "rate_limited":
            self._backoff_until = now + BACKOFF_S
            self._save_cache()

    def _load_cache(self, now: float) -> None:
        """Adopt a newer reading or a live backoff from the shared file."""
        if self._cache is None:
            return
        try:
            raw = json.loads(self._cache.read_text())
            backoff = now + (float(raw.get("backoff_until_wall", 0.0)) - self._wall())
            snap = None
            # A 429 before any reading still writes the file, for its backoff.
            if raw.get("windows") is not None:
                fetched = now - (self._wall() - float(raw["fetched_wall"]))
                snap = QuotaSnapshot(
                    windows=tuple(
                        QuotaWindow(
                            w["kind"],
                            float(w["percent"]),
                            w["severity"],
                            _timestamp(w["resets_at"]),
                            bool(w["is_active"]),
                        )
                        for w in raw["windows"]
                    ),
                    fetched_at=fetched,
                )
        except (OSError, ValueError, KeyError, TypeError):
            return
        # +1 s: our own write comes back through float round-trips.
        if snap is not None and (
            self._snapshot is None or snap.fetched_at > self._snapshot.fetched_at + 1
        ):
            self._snapshot = snap
            self._failure = ""
        if backoff > now and backoff > self._backoff_until:
            self._backoff_until = backoff
            self._failure = "rate_limited"

    def _save_cache(self) -> None:
        if self._cache is None:
            return
        now, wall = self._clock(), self._wall()
        snap = self._snapshot
        raw = {
            "fetched_wall": wall - (now - snap.fetched_at) if snap else None,
            "backoff_until_wall": wall + (self._backoff_until - now)
            if self._backoff_until > now
            else 0.0,
            "windows": [
                {
                    "kind": w.kind,
                    "percent": w.percent,
                    "severity": w.severity,
                    "resets_at": w.resets_at.isoformat() if w.resets_at else None,
                    "is_active": w.is_active,
                }
                for w in snap.windows
            ]
            if snap
            else None,
        }
        try:
            self._cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._cache.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(raw))
            os.replace(tmp, self._cache)
        except OSError:
            pass

    def current(self) -> QuotaState:
        now = self._clock()
        age = 0.0
        if self._snapshot is not None:
            age = max(0.0, now - self._snapshot.fetched_at)
        return QuotaState(
            snapshot=self._snapshot,
            age_s=age,
            failure=self._failure,
            retry_in_s=max(0.0, self._backoff_until - now),
        )


FAILURE_TEXT = {
    "no_credentials": "no credentials",
    "unauthorized": "auth expired",
    "rate_limited": "rate limited",
    "unreachable": "unreachable",
}
