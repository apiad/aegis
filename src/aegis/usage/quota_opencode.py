"""Live OpenCode Go quota — the rolling, weekly and monthly windows.

OpenCode stores its API keys locally; the Zen gateway reports the Go plan's
current window utilisation behind one of them. This module reads the key, asks
the endpoint, and hands back the same ``QuotaSnapshot`` the Claude provider
produces, so the service, the renderer and the status bar stay generic.

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

URL = "https://opencode.ai/zen/go/v1/usage"

# The endpoint's own window names, in the order they are worth reading. Any
# other key in ``usage`` is ignored rather than guessed at.
WINDOW_KINDS = ("rolling", "weekly", "monthly")


def _user_agent() -> str:
    try:
        from importlib.metadata import version
        return f"aegis/{version('aegis-harness')}"
    except Exception:  # noqa: BLE001 — an unknown version must not block a read
        return "aegis/0"


def auth_path() -> Path:
    """Where OpenCode keeps its API keys. ``OPENCODE_AUTH`` overrides."""
    share = os.environ.get(
        "XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
    default = Path(share) / "opencode" / "auth.json"
    return Path(os.environ.get("OPENCODE_AUTH", str(default)))


def read_key(path: Path | None = None) -> str | None:
    """The Go plan's API key, or None if there isn't a usable one.

    Never raises: a missing file, unreadable JSON and an absent entry are all
    the same answer — we cannot ask about quota. Note this is the ``opencode-go``
    entry specifically, not the ``opencode`` (Zen pay-as-you-go) one; they are
    separate accounts even when they currently hold the same key.
    """
    p = path or auth_path()
    try:
        with Path(p).open() as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    entry = data.get("opencode-go")
    if not isinstance(entry, dict):
        return None
    key = entry.get("key")
    return key if isinstance(key, str) and key else None


def parse_usage(payload: dict, *, now: float) -> QuotaSnapshot:
    """Build a snapshot from the response's ``usage`` object.

    Entries that cannot be read are dropped rather than failing the whole
    snapshot — the shape is not contractual. Every window counts, so unlike the
    Anthropic payload there is no per-window active flag to honour.
    """
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return QuotaSnapshot(windows=(), fetched_at=now)

    windows: list[QuotaWindow] = []
    for kind in WINDOW_KINDS:
        raw = usage.get(kind)
        if not isinstance(raw, dict):
            continue
        percent = raw.get("percent")
        if not isinstance(percent, (int, float)) or isinstance(percent, bool):
            continue
        windows.append(QuotaWindow(
            kind=kind,
            percent=float(percent),
            # ``status`` is "ok" in the healthy case, which is not one of the
            # severities we render — so this falls through to the percent
            # thresholds, while still honouring a real severity if one appears.
            severity=_severity(float(percent), raw.get("status")),
            resets_at=_timestamp(raw.get("resetsAt")),
            is_active=True,
        ))
    return QuotaSnapshot(windows=tuple(windows), fetched_at=now)


def fetch_usage(key: str, *, timeout: float = 10.0,
                opener=None, now: float | None = None) -> QuotaSnapshot:
    """Ask the endpoint. Blocking — callers run it off the event loop.

    ``opener`` is the injection seam for tests; it defaults to
    ``urllib.request.urlopen``.
    """
    request = urllib.request.Request(URL, headers={
        "Authorization": f"Bearer {key}",
        # Cloudflare fronts this endpoint and rejects urllib's default agent
        # with 403 "error code: 1010". Identify honestly instead of spoofing;
        # any real agent string is accepted.
        "User-Agent": _user_agent(),
    })
    open_url = opener or urllib.request.urlopen
    try:
        with open_url(request, timeout=timeout) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            kind = "unauthorized"
        elif exc.code == 429:
            kind = "rate_limited"
        else:
            kind = "unreachable"
        raise QuotaError(kind) from exc
    except Exception as exc:  # noqa: BLE001 — every other failure is the same
        raise QuotaError("unreachable") from exc
    if not isinstance(payload, dict):
        raise QuotaError("unreachable")
    return parse_usage(
        payload, now=time.monotonic() if now is None else now)


PROVIDER = QuotaProvider(
    name="opencode-go",
    label="oc",
    harness="opencode",
    bar_windows=(("rolling", "5h"), ("weekly", "wk"), ("monthly", "mo")),
    fetch=fetch_usage,
    read_token=read_key,
)
