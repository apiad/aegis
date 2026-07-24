"""Live Claude subscription quota — the 5-hour and weekly windows.

Claude Code stores an OAuth token locally; Anthropic exposes current window
utilisation at an undocumented endpoint behind it. This module reads the token,
asks the endpoint, and caches the answer.

The endpoint is undocumented and may change or vanish. Every failure path here
degrades to a message in the status bar; nothing raises into the render loop.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

URL = "https://api.anthropic.com/api/oauth/usage"
BETA = "oauth-2025-04-20"

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


def credentials_path() -> Path:
    """Where Claude Code keeps its OAuth token. ``CLAUDE_CREDS`` overrides."""
    default = Path.home() / ".claude" / ".credentials.json"
    return Path(os.environ.get("CLAUDE_CREDS", str(default)))


def read_token(path: Path | None = None) -> str | None:
    """The OAuth access token, or None if there isn't a usable one.

    Never raises: a missing file, unreadable JSON and an absent field are all
    the same answer — we cannot ask about quota.
    """
    p = path or credentials_path()
    try:
        with Path(p).open() as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    token = (data.get("claudeAiOauth") or {}).get("accessToken")
    return token or None


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


def parse_quota(payload: dict, *, now: float) -> QuotaSnapshot:
    """Build a snapshot from the response's ``limits`` array.

    The array is preferred over the ``five_hour`` / ``seven_day`` headline
    objects: it is the complete set of windows, and each entry carries the
    API's own ``severity``. Entries that cannot be read are dropped rather than
    failing the whole snapshot — the payload is null-heavy and its shape is not
    contractual.
    """
    windows: list[QuotaWindow] = []
    for raw in payload.get("limits") or ():
        if not isinstance(raw, dict):
            continue
        kind = raw.get("kind")
        percent = raw.get("percent")
        if not isinstance(kind, str) or not kind:
            continue
        if not isinstance(percent, (int, float)) or isinstance(percent, bool):
            continue
        windows.append(QuotaWindow(
            kind=kind,
            percent=float(percent),
            severity=_severity(float(percent), raw.get("severity")),
            resets_at=_timestamp(raw.get("resets_at")),
            is_active=bool(raw.get("is_active")),
        ))
    return QuotaSnapshot(windows=tuple(windows), fetched_at=now)


def fetch_quota(token: str, *, timeout: float = 10.0,
                opener=None, now: float | None = None) -> QuotaSnapshot:
    """Ask the endpoint. Blocking — callers run it off the event loop.

    ``opener`` is the injection seam for tests; it defaults to
    ``urllib.request.urlopen``.
    """
    request = urllib.request.Request(URL, headers={
        "Authorization": f"Bearer {token}",
        "anthropic-beta": BETA,
    })
    open_url = opener or urllib.request.urlopen
    try:
        with open_url(request, timeout=timeout) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            kind = "unauthorized"
        elif exc.code == 429:
            # The endpoint throttles. Distinct from unreachable because the
            # right response is to back off, not to retry on the usual cadence.
            kind = "rate_limited"
        else:
            kind = "unreachable"
        raise QuotaError(kind) from exc
    except Exception as exc:  # noqa: BLE001 — every other failure is the same
        raise QuotaError("unreachable") from exc
    if not isinstance(payload, dict):
        raise QuotaError("unreachable")
    return parse_quota(
        payload, now=time.monotonic() if now is None else now)


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

    def __init__(self, *, clock=time.monotonic, fetch=None,
                 token_reader=None) -> None:
        self._clock = clock
        self._fetch = fetch or fetch_quota
        self._read_token = token_reader or read_token
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


# Window kinds worth a place on a one-line status bar, in display order.
_BAR_WINDOWS = (("session", "5h"), ("weekly_all", "wk"))

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


def format_quota_tiers(state: QuotaState, colors,
                       *, now: datetime | None = None) -> tuple[str, ...]:
    """Render the quota segment, widest form first.

    An empty tuple means "say nothing" — there is no reading and no failure to
    report, which is only true before the first poll completes.
    """
    if state.snapshot is None:
        if not state.failure:
            return ()
        text = _FAILURE_TEXT.get(state.failure, "unreachable")
        return (f"[{colors.muted}]⧗ quota — {text}[/]",)

    moment = now or datetime.now(timezone.utc)
    stale = bool(state.failure)
    parts: list[str] = []
    shorts: list[str] = []
    for kind, label in _BAR_WINDOWS:
        window = state.snapshot.window(kind)
        if window is None:
            continue
        value = f"{window.percent:.0f}%"
        shorts.append(f"{window.percent:.0f}")
        if not stale and window.severity == "warning":
            value = f"[{colors.working}]{value}[/]"
        elif not stale and window.severity == "critical":
            value = f"[{colors.error}]{value}[/]"
        chunk = f"{label} {value}"
        # The reset time is only a question once the number is high.
        if window.severity != "normal" and window.resets_at is not None:
            chunk += f" ⟶{_countdown(window.resets_at, moment)}"
        parts.append(chunk)

    if not parts:
        return ()

    full = "⧗ " + " · ".join(parts)
    short = "⧗ " + "/".join(shorts) + "%"
    if stale:
        full = f"[{colors.muted}]{full} ({_age(state.age_s)} old)[/]"
        short = f"[{colors.muted}]{short}[/]"
    return (full, short)


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
