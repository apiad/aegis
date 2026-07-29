"""Hand a path or URL to the desktop's own handler.

Alt+click on a transcript block opens its file the way double-clicking
would in a file manager — in your editor, image viewer, or browser —
rather than in aegis's read-only file tab (ctrl+click).

Deliberately not ``webbrowser.open``: on Linux that would cheerfully
open a ``.py`` in Firefox instead of the registered editor.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

_URL_RE = re.compile(r"^(https?|ftp)://\S+$", re.IGNORECASE)


def is_url(token: str) -> bool:
    return bool(_URL_RE.match(token.strip()))


def native_open_command(target: str) -> list[str] | None:
    """The platform's "open this with whatever handles it" command, or
    None where there is no such thing (headless box, bare SSH)."""
    if sys.platform == "darwin":
        exe = shutil.which("open")
    elif sys.platform.startswith("linux"):
        exe = shutil.which("xdg-open")
    else:
        exe = None
    return [exe, target] if exe else None


def refuse_reason(path: Path) -> str | None:
    """Why this path must not be handed to the desktop, or None.

    A ``.desktop`` file is *executed* by the handler rather than shown.
    Everything else at worst displays content, so it goes through — but
    the path came out of agent output, and "click to run" is not what
    the gesture promises.
    """
    if path.suffix == ".desktop":
        return "refusing to open a .desktop file (it would run, not open)"
    return None


def open_native(target: str) -> str | None:
    """Launch the desktop handler, detached. Returns an error message on
    failure, None when it was handed off."""
    cmd = native_open_command(target)
    if cmd is None:
        return "no xdg-open/open on this host"
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        return f"could not open: {e}"
    return None
