"""Live Claude subscription quota — the 5-hour and weekly windows.

Claude Code stores an OAuth token locally; Anthropic exposes current window
utilisation at an undocumented endpoint behind it. This module reads the token,
asks the endpoint, and hands back the ``QuotaSnapshot`` that ``quota.py``'s
service and renderer consume.

The endpoint is undocumented and may change or vanish. Every failure path here
degrades to a note on the gauges; nothing raises into the caller.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from .core import (
    boot_clock,
    QuotaError,
    QuotaProvider,
    QuotaSnapshot,
    QuotaWindow,
    _severity,
    _timestamp,
)

URL = "https://api.anthropic.com/api/oauth/usage"
BETA = "oauth-2025-04-20"


def credentials_path() -> Path:
    """Where Claude Code keeps its OAuth token. ``CLAUDE_CREDS`` overrides."""
    default = Path.home() / ".claude" / ".credentials.json"
    return Path(os.environ.get("CLAUDE_CREDS", str(default)))


def config_path() -> Path:
    """Claude Code's own config, which names the signed-in account.
    ``CLAUDE_CONFIG`` overrides."""
    return Path(os.environ.get("CLAUDE_CONFIG", str(Path.home() / ".claude.json")))


def account_id(path: Path | None = None) -> str | None:
    """The signed-in account's uuid, or None. Never raises."""
    try:
        with (path or config_path()).open() as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    uuid = (data.get("oauthAccount") or {}).get("accountUuid")
    return uuid if isinstance(uuid, str) and uuid else None


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
        windows.append(
            QuotaWindow(
                kind=kind,
                percent=float(percent),
                severity=_severity(float(percent), raw.get("severity")),
                resets_at=_timestamp(raw.get("resets_at")),
                is_active=bool(raw.get("is_active")),
            )
        )
    return QuotaSnapshot(windows=tuple(windows), fetched_at=now)


def fetch_quota(
    token: str, *, timeout: float = 10.0, opener=None, now: float | None = None
) -> QuotaSnapshot:
    """Ask the endpoint. Blocking — callers run it off the event loop.

    ``opener`` is the injection seam for tests; it defaults to
    ``urllib.request.urlopen``.
    """
    request = urllib.request.Request(
        URL,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": BETA,
        },
    )
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
    return parse_quota(payload, now=boot_clock() if now is None else now)


PROVIDER = QuotaProvider(
    name="claude",
    label="Claude",
    harness="claude-code",
    # The two windows the gauges draw; the payload has more.
    bar_windows=(("session", "5 hours"), ("weekly_all", "week")),
    fetch=fetch_quota,
    read_token=read_token,
    account=account_id,
    # The usage endpoint 429s at a minute per process (#41). Three minutes,
    # shared through the cache, and a turn end may not ask sooner than one.
    poll_s=180.0,
    turn_floor_s=60.0,
    # The five-hour window starts at the first message of a session and the
    # weekly ones roll on a fixed boundary, so in both cases the start is
    # `resets_at` minus the span. The payload says neither.
    window_spans={
        "session": 5 * 3600,
        "weekly_all": 7 * 86400,
        "weekly_opus": 7 * 86400,
        # Live in the payload as of 2026-09-27, resetting on the same boundary
        # as weekly_all. A kind we get wrong here loses its projection, it does
        # not gain a wrong one: an over-long reset trips the skew guard.
        "weekly_scoped": 7 * 86400,
    },
)
