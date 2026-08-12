"""Subscription-quota core, shared by every provider.

A provider module (``quota_claude``, ``quota_opencode``) knows one vendor's
credentials, endpoint and payload shape, and produces a ``QuotaSnapshot``.
Everything downstream of that snapshot — polling, staleness, severity, the
status-bar segment, the ``/usage quota`` breakdown — lives here and knows
nothing about any particular vendor.

``QuotaProvider`` is the seam: it carries the callables and the window list a
provider declares, so adding a third vendor is a new module plus a registry
entry, not a change to the service or the renderer.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

# Used only when the API omits its own `severity` for a window.
WARNING_AT = 80.0
CRITICAL_AT = 95.0


class QuotaError(Exception):
    """A fetch failed.

    ``kind`` is ``unauthorized``, ``rate_limited`` or ``unreachable``.
    """

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


@dataclass(frozen=True)
class QuotaWindow:
    kind: str                    # "session", "weekly_all", "weekly_opus", ...
    percent: float
    severity: str                # "normal" | "warning" | "critical"
    resets_at: datetime | None
    is_active: bool


@dataclass(frozen=True)
class QuotaSnapshot:
    windows: tuple[QuotaWindow, ...]
    fetched_at: float            # monotonic

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
    name: str                                   # "claude" | "opencode-go"
    label: str                                  # bar prefix when >1 is shown
    harness: str                                # matches an Agent's harness
    bar_windows: tuple[tuple[str, str], ...]    # (kind, label), display order
    fetch: Callable[..., "QuotaSnapshot"]
    read_token: Callable[..., str | None]


def _severity(percent: float, given) -> str:
    if given in ("normal", "warning", "critical"):
        return given
    if percent >= CRITICAL_AT:
        return "critical"
    if percent >= WARNING_AT:
        return "warning"
    return "normal"


def _timestamp(raw) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


POLL_S = 60.0          # background cadence
STALE_DROP_S = 300.0   # how long a stale value stays on screen before it goes
BACKOFF_S = 300.0      # hands off the endpoint after it says 429


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
    failure: str = ""   # "" | "no_credentials" | "unauthorized" | "unreachable"


class QuotaService:
    """Polls the quota endpoint on a cadence and caches the answer.

    ``current()`` is synchronous so the 1-second UI tick never touches the
    network. Fetches run in a worker thread; the endpoint is blocking stdlib
    code and must not stall the event loop.
    """

    def __init__(self, *, fetch, token_reader, clock=time.monotonic) -> None:
        self._clock = clock
        self._fetch = fetch
        self._read_token = token_reader
        self._snapshot: QuotaSnapshot | None = None
        self._failure = ""
        self._failing_since = 0.0
        self._last_attempt = 0.0
        self._backoff_until = 0.0
        self._task = None

    @property
    def started(self) -> bool:
        return self._task is not None

    def start(self) -> None:
        """Begin polling. Idempotent — safe to call from every UI tick."""
        if self._task is not None:
            return
        import asyncio
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        import asyncio
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
            await asyncio.sleep(POLL_S)

    async def refresh(self, *, force: bool = False,
                      min_interval: float | None = None) -> None:
        """Fetch unless we fetched recently.

        ``min_interval`` overrides the default floor — the turn-end trigger
        passes a shorter one so a finished turn updates the number promptly
        without letting a burst of short turns hammer an undocumented endpoint.

        ``force`` skips the floor but never the 429 backoff: once the endpoint
        has asked us to stop, an explicit ``/usage quota`` must not override it.
        """
        now = self._clock()
        if now < self._backoff_until:
            return
        floor = POLL_S if min_interval is None else min_interval
        if not force and self._last_attempt and now - self._last_attempt < floor:
            return
        self._last_attempt = now

        token = self._read_token()
        if not token:
            self._snapshot = None
            self._failure = "no_credentials"
            self._failing_since = 0.0
            return

        try:
            import asyncio
            snapshot = await asyncio.to_thread(self._fetch, token)
        except QuotaError as exc:
            self._note_failure(exc.kind, now)
            return
        except Exception:  # noqa: BLE001 — a fetch must never break the caller
            self._note_failure("unreachable", now)
            return
        self._snapshot = snapshot
        self._failure = ""
        self._failing_since = 0.0
        self._backoff_until = 0.0

    def _note_failure(self, kind: str, now: float) -> None:
        self._failure = kind
        if kind == "rate_limited":
            self._backoff_until = now + BACKOFF_S
        if self._snapshot is None:
            return
        if not self._failing_since:
            self._failing_since = now
        elif now - self._failing_since >= STALE_DROP_S:
            self._snapshot = None

    def current(self) -> QuotaState:
        age = 0.0
        if self._snapshot is not None:
            age = max(0.0, self._clock() - self._snapshot.fetched_at)
        return QuotaState(
            snapshot=self._snapshot, age_s=age, failure=self._failure)


_FAILURE_TEXT = {
    "no_credentials": "no credentials",
    "unauthorized": "auth expired",
    "rate_limited": "rate limited",
    "unreachable": "unreachable",
}


def _countdown(target: datetime, now: datetime) -> str:
    """``2h14m`` / ``12m`` / ``now`` — how long until the window resets."""
    seconds = int((target - now).total_seconds())
    if seconds <= 0:
        return "now"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"
    return f"{seconds // 86400}d{(seconds % 86400) // 3600}h"


def _age(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    return f"{int(seconds // 3600)}h"


def _paint(text: str, severity: str, colors, stale: bool) -> str:
    """Colour a percentage by severity. A stale reading is never coloured —
    the number may have moved since, so an alarm would be asserting more than
    we know."""
    if stale or severity == "normal":
        return text
    tint = colors.working if severity == "warning" else colors.error
    return f"[{tint}]{text}[/]"


def _provider_tiers(provider: "QuotaProvider", state: QuotaState, colors,
                    *, label: str, moment: datetime) -> tuple[str, str, str]:
    """One provider's three forms, or ``()`` when it has nothing to say.

    ``label`` is empty when this provider is the only one on the bar — there is
    nothing to disambiguate it from, so today's unprefixed reading survives.
    """
    prefix = f"{label} " if label else ""
    if state.snapshot is None:
        # No credentials is not a failure: it means this is not your provider,
        # and the bar should not nag you about an account you do not have.
        if not state.failure or state.failure == "no_credentials":
            return ()
        text = _FAILURE_TEXT.get(state.failure, "unreachable")
        body = f"{prefix}{text}" if label else f"quota — {text}"
        return (body, body, body)

    stale = bool(state.failure)
    parts: list[str] = []
    shorts: list[str] = []
    worst: QuotaWindow | None = None
    for kind, name in provider.bar_windows:
        window = state.snapshot.window(kind)
        if window is None:
            continue
        if worst is None or window.percent > worst.percent:
            worst = window
        value = _paint(f"{window.percent:.0f}%", window.severity, colors, stale)
        shorts.append(f"{window.percent:.0f}")
        chunk = f"{name} {value}"
        # The reset time is only a question once the number is high.
        if window.severity != "normal" and window.resets_at is not None:
            chunk += f" ⟶{_countdown(window.resets_at, moment)}"
        parts.append(chunk)

    if not parts or worst is None:
        return ()

    full = prefix + " · ".join(parts)
    mid = prefix + "/".join(shorts) + "%"
    least = _paint(
        f"{label}{worst.percent:.0f}" if label else f"{worst.percent:.0f}%",
        worst.severity, colors, stale)
    if stale:
        full = f"[{colors.muted}]{full} ({_age(state.age_s)} old)[/]"
        mid = f"[{colors.muted}]{mid}[/]"
        least = f"[{colors.muted}]{least}[/]"
    return (full, mid, least)


def format_quota_bar(readings, colors,
                     *, now: datetime | None = None) -> tuple[str, ...]:
    """Render every provider's quota as one segment, widest form first.

    ``readings`` is a sequence of ``(QuotaProvider, QuotaState)``. Providers
    with nothing to say — no credentials, or no reading yet — drop out before
    labelling is decided, so holding one account renders exactly as it did
    before a second provider existed.

    An empty tuple means "say nothing".
    """
    moment = now or datetime.now(timezone.utc)
    live = [(p, s) for p, s in readings
            if _provider_tiers(p, s, colors, label="", moment=moment)]
    if not live:
        return ()

    label_them = len(live) > 1
    tiers = [_provider_tiers(p, s, colors,
                             label=p.label if label_them else "", moment=moment)
             for p, s in live]
    return (
        "⧗ " + " │ ".join(t[0] for t in tiers),
        "⧗ " + " │ ".join(t[1] for t in tiers),
        "⧗ " + " ".join(t[2] for t in tiers),
    )


def quota_lines(state: QuotaState, *,
                now: datetime | None = None) -> list[str]:
    """Full breakdown for ``/usage quota`` — every window, not just the pair
    the status bar has room for."""
    moment = now or datetime.now(timezone.utc)
    if state.snapshot is None:
        text = _FAILURE_TEXT.get(state.failure or "unreachable", "unreachable")
        return [f"quota unavailable — {text}"]

    lines: list[str] = []
    for window in state.snapshot.windows:
        resets = "—"
        if window.resets_at is not None:
            stamp = window.resets_at.astimezone().strftime("%Y-%m-%d %H:%M")
            resets = f"{stamp} ({_countdown(window.resets_at, moment)})"
        active = "active" if window.is_active else "idle"
        lines.append(
            f"{window.kind:<14} {window.percent:>5.0f}%  "
            f"{window.severity:<8} {active:<6} resets {resets}")

    footer = f"read {_age(state.age_s)} ago"
    if state.failure:
        footer += f" — fetch failing ({state.failure})"
    lines.append("")
    lines.append(footer)
    return lines


def quota_report(readings, *, now: datetime | None = None) -> list[str]:
    """``/usage quota`` across every provider.

    Providers you hold no credentials for are left out entirely, on the same
    reasoning as the status bar. One surviving provider reports exactly as it
    did before a second existed; more than one gets a name heading each.
    """
    live = [(p, s) for p, s in readings
            if s.snapshot is not None or (
                s.failure and s.failure != "no_credentials")]
    if not live:
        return ["quota unavailable — no credentials"]
    if len(live) == 1:
        return quota_lines(live[0][1], now=now)

    lines: list[str] = []
    for provider, state in live:
        if lines:
            lines.append("")
        lines.append(provider.name)
        lines.extend(quota_lines(state, now=now))
    return lines
