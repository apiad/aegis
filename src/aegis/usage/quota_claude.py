"""Live Claude subscription quota — the 5-hour and weekly windows.

Claude Code stores an OAuth token locally; Anthropic exposes current window
utilisation at an undocumented endpoint behind it. This module reads the token,
asks the endpoint, and hands back the ``QuotaSnapshot`` that ``quota.py``'s
service and renderer consume.

The endpoint is undocumented and may change or vanish. Every failure path here
degrades to a message in the status bar; nothing raises into the render loop.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from aegis.usage.quota import (
    QuotaError, QuotaProvider, QuotaSnapshot, QuotaWindow, _severity,
    _timestamp,
)

URL = "https://api.anthropic.com/api/oauth/usage"
BETA = "oauth-2025-04-20"


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


PROVIDER = QuotaProvider(
    name="claude",
    label="cc",
    harness="claude-code",
    # The pair the bar has always shown; the account has more windows than
    # fit on one line, and /usage quota still lists all of them.
    bar_windows=(("session", "5h"), ("weekly_all", "wk")),
    fetch=fetch_quota,
    read_token=read_token,
)
