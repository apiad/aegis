"""Opening the server in a browser window of its own (``aegis``, ``serve --window``).

A Chromium-family browser run with ``--app=<url>`` opens a window with no tabs
and no address bar, so aegis looks like an app and the token in the URL stays
out of sight. Any other browser only gets a tab, through ``webbrowser``.

The browser is started in its own session with its output discarded: stopping
the server must not take the window with it, and the window reconnects when
the server comes back.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import webbrowser
from pathlib import Path

from websockets.exceptions import WebSocketException
from websockets.sync.client import connect
from websockets.typing import Origin

from .web import PROTO

# Tried after the desktop's default browser, in this order. On zion
# `google-chrome` is the stable build while the default is the beta, which is
# why the default comes first: the window belongs in the browser that is logged in.
CANDIDATES = (
    "google-chrome",
    "google-chrome-stable",
    "google-chrome-beta",
    "chromium",
    "chromium-browser",
    "microsoft-edge",
    "brave-browser",
)
MAC_APPS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)

PROBE_TIMEOUT_S = 2.0


def desktop_default() -> str | None:
    """The default browser's name on a freedesktop system, e.g.
    ``google-chrome-beta``; None where there is no ``xdg-settings``."""
    exe = shutil.which("xdg-settings")
    if exe is None:
        return None
    try:
        out = subprocess.run(
            [exe, "get", "default-web-browser"],
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.removesuffix(".desktop") or None


def find_browser(asked: str | None) -> str | None:
    """The browser to run: the one asked for, else the desktop's default if it
    is Chromium-family, else the first one installed.

    None means no Chromium-family browser was found."""
    if asked:
        return asked
    default = desktop_default()
    if default in CANDIDATES and (found := shutil.which(default)):
        return found
    for name in CANDIDATES:
        if found := shutil.which(name):
            return found
    return next((app for app in MAC_APPS if Path(app).is_file()), None)


def open_window(url: str, browser: str | None) -> None:
    exe = find_browser(browser)
    if exe is None:
        webbrowser.open(url)
        return
    subprocess.Popen(
        [exe, f"--app={url}"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def serves(host: str, port: int, token: str) -> bool:
    """Whether the server on ``host:port`` is aegis holding ``token``.

    The token is per state directory, so a yes means this root's server: only
    it answers the hello with ``welcome``. Anything else on the port (another
    program, another root's aegis) is a no."""
    try:
        with connect(
            f"ws://{host}:{port}/ws",
            origin=Origin(f"http://{host}:{port}"),
            open_timeout=PROBE_TIMEOUT_S,
            close_timeout=PROBE_TIMEOUT_S,
            proxy=None,  # an http_proxy in the environment must not see loopback
        ) as ws:
            ws.send(json.dumps({"t": "hello", "token": token, "proto": PROTO}))
            reply = json.loads(ws.recv(timeout=PROBE_TIMEOUT_S))
    except (OSError, TimeoutError, WebSocketException, ValueError):
        return False
    return isinstance(reply, dict) and reply.get("t") == "welcome"
