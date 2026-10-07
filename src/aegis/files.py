"""Files an agent hands to the person: classified, copied, served.

A sent file is copied into ``<state>/files/<id>/<name>`` when it is sent, because
agents overwrite and delete their working files and an archived session must
still show what it was sent. ``<id>`` is 128 random bits and the URL is the
capability: the token lives in the tab's ``sessionStorage`` and only the
websocket carries it, so an ``<img>``, a new tab and a download could not.

The preview kind is decided here, once, at send (the fold draws nothing it did
not decide). HTML, SVG and XML can run script; on aegis's origin a script could
read the token and drive every agent, so they are served with ``CSP: sandbox``
(``allow-scripts`` for HTML, so an interactive report still runs), which gives
them an opaque origin. PDF is not: Chrome refuses to render a PDF
under that policy, and its viewer cannot reach the page's storage.
"""

from __future__ import annotations

import mimetypes
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

MAX_BYTES = 100 * 1024 * 1024
EXCERPT_LINES = 40
EXCERPT_BYTES = 4096
SNIFF_BYTES = 4096

_ID = re.compile(r"[A-Za-z0-9_-]{16,64}")
_TEXT_APPS = ("json", "yaml", "xml", "javascript", "toml", "x-sh", "sql")


class FileError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _sniff_text(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            head = f.read(SNIFF_BYTES)
    except OSError:
        return False
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as e:
        # A multi-byte character cut by the 4 KB window is still text.
        return e.start >= len(head) - 3
    return True


def classify(path: Path) -> tuple[str, str]:
    """(mime, preview), the preview one of image, pdf, html, markdown, text,
    audio, video, other."""
    mime = mimetypes.guess_type(path.name)[0] or ""
    if path.suffix.lower() in (".md", ".markdown"):
        return "text/markdown", "markdown"
    if not mime or mime == "application/octet-stream":
        return (
            ("text/plain", "text")
            if _sniff_text(path)
            else ("application/octet-stream", "other")
        )
    major, _, minor = mime.partition("/")
    if major == "image":
        return mime, "image"
    if mime == "application/pdf":
        return mime, "pdf"
    if mime in ("text/html", "application/xhtml+xml"):
        return mime, "html"
    if mime == "text/markdown":
        return mime, "markdown"
    if major == "text" or (
        major == "application" and any(t in minor for t in _TEXT_APPS)
    ):
        return mime, "text"
    if major in ("audio", "video"):
        return mime, major
    return mime, "other"


def excerpt(path: Path) -> str | None:
    """The first 40 lines or 4 KB, whichever is shorter, cut at a line."""
    with path.open("rb") as f:
        head = f.read(EXCERPT_BYTES + 1)
    text = head.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if len(head) > EXCERPT_BYTES:
        lines = lines[:-1]  # the last line may be cut
    out: list[str] = []
    size = 0
    for line in lines[:EXCERPT_LINES]:
        size += len(line.encode()) + 1
        if size > EXCERPT_BYTES:
            break
        out.append(line)
    return "\n".join(out)


def human_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    size, unit = n / 1024, "KB"
    if size >= 1024:
        size, unit = size / 1024, "MB"
    return f"{size:.1f} {unit}" if size < 10 else f"{size:.0f} {unit}"


def url(file_id: str, name: str) -> str:
    return f"/files/{file_id}/{quote(name)}"


def store(state_root: Path, src: Path) -> dict:
    """Copy ``src`` under a fresh id; the store record's body, without caption."""
    if not src.exists():
        raise FileError("not_found", f"{src} does not exist")
    if not src.is_file():
        raise FileError("not_a_file", f"{src} is not a regular file")
    size = src.stat().st_size
    if size > MAX_BYTES:
        raise FileError("too_large", f"{src} is {size} bytes; the limit is {MAX_BYTES}")
    mime, preview = classify(src)
    file_id = secrets.token_urlsafe(16)
    root = state_root / "files"
    root.mkdir(parents=True, exist_ok=True)
    # Copied beside the id folders, under a name no id can match, then moved
    # in: a half-copied file is never at a URL, and any name, dotfiles
    # included, can be served.
    part = root / f".{file_id}.part"
    try:
        shutil.copyfile(src, part)
    except OSError as e:
        part.unlink(missing_ok=True)
        raise FileError("unreadable", f"cannot read {src}: {e}") from e
    folder = root / file_id
    folder.mkdir()
    dest = folder / src.name
    os.replace(part, dest)
    return {
        "kind": "file",
        "file_id": file_id,
        "name": src.name,
        "mime": mime,
        "size": size,
        "preview": preview,
        "excerpt": excerpt(dest) if preview in ("markdown", "text") else None,
    }


def find(state_root: Path, file_id: str, name: str) -> Path | None:
    if not _ID.fullmatch(file_id) or "/" in name or name in (".", ".."):
        return None
    path = state_root / "files" / file_id / name
    return path if path.is_file() else None


def headers(path: Path, download: bool) -> dict[str, str]:
    mime, preview = classify(path)
    h = {
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Cache-Control": "private, max-age=31536000, immutable",
        "Content-Type": mime,
    }
    if download or preview == "other":
        h["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(path.name)}"
        return h
    if preview in ("markdown", "text"):
        h["Content-Type"] = "text/plain; charset=utf-8"
    if preview == "html":
        # Scripts run, so an interactive report works, in an opaque origin.
        h["Content-Security-Policy"] = "sandbox allow-scripts"
    elif "svg" in mime or "xml" in mime:
        h["Content-Security-Policy"] = "sandbox"
    return h


def opener() -> list[str] | None:
    """The command that opens a file in the desktop's app for it, or None when
    the server has no desktop to open it on. ``AEGIS_OPENER`` overrides it.

    A browser on loopback is not proof of a desktop: an SSH tunnel to a
    headless box looks local, and ``xdg-open`` there would open nothing."""
    if custom := os.environ.get("AEGIS_OPENER"):
        return shlex.split(custom)
    if sys.platform == "darwin":
        cmd = "open"
    elif os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        cmd = "xdg-open"
    else:
        return None
    found = shutil.which(cmd)
    return [found] if found else None


def open_natively(path: Path) -> subprocess.Popen:
    """Hand ``path`` to the desktop, detached, and return without waiting."""
    cmd = opener()
    if cmd is None:
        raise FileError("no_desktop", "this server has no desktop to open files on")
    return subprocess.Popen(
        [*cmd, str(path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
