"""Files an agent hands to the person: classified, copied, served.

A sent file is copied into ``<state>/files/<id>/<name>`` when it is sent, because
agents overwrite and delete their working files and an archived session must
still show what it was sent. ``<id>`` is 128 random bits and the URL is the
capability: the websocket needs the server's token, and an ``<img>``, a new
tab and a download cannot prove it.

The preview kind is decided here, once, at send (the fold draws nothing it did
not decide). HTML, SVG and XML can run script; on aegis's origin a script could
open the websocket, which the browser would sign in with its cookie, and drive
every agent, so they are served with ``CSP: sandbox``
(``allow-scripts`` for HTML, so an interactive report still runs), which gives
them an opaque origin. PDF is not: Chrome refuses to render a PDF
under that policy, and its viewer cannot reach the page's storage.
"""

from __future__ import annotations

import asyncio
import contextlib
import mimetypes
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from . import launch
from .as_user import Runner

MAX_BYTES = 100 * 1024 * 1024
EXCERPT_LINES = 40
EXCERPT_BYTES = 4096
SNIFF_BYTES = 4096

_ID = re.compile(r"[A-Za-z0-9_-]{16,64}")
_TEXT_APPS = ("json", "yaml", "xml", "javascript", "toml", "x-sh", "sql")


class FileError(Exception):
    def __init__(self, code: str, message: str, path: Path | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.path = path


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
    return classify_name(path.name, lambda: _sniff_text(path))


def classify_name(name: str, sniff=lambda: False) -> tuple[str, str]:
    """``classify`` from a name alone; ``sniff`` says whether an unknown type is
    text, and a file not on this disk is never sniffed."""
    mime = mimetypes.guess_type(name)[0] or ""
    if Path(name).suffix.lower() in (".md", ".markdown"):
        return "text/markdown", "markdown"
    if not mime or mime == "application/octet-stream":
        return (
            ("text/plain", "text") if sniff() else ("application/octet-stream", "other")
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
    # Whole parts of the subtype only: "openxmlformats" (.docx, .xlsx, .pptx)
    # holds "xml" and is a zip, whose excerpt would be bytes drawn as text.
    parts = {minor, *re.split(r"[+.-]", minor)}
    if major == "text" or (major == "application" and parts & set(_TEXT_APPS)):
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


def check(src: Path) -> int:
    """``src``'s size, or the FileError that sending it would raise."""
    if not src.exists():
        raise FileError("not_found", f"{src} does not exist", src)
    if not src.is_file():
        raise FileError("not_a_file", f"{src} is not a regular file", src)
    if not os.access(src, os.R_OK, effective_ids=True):
        raise FileError("unreadable", f"cannot read {src}", src)
    size = src.stat().st_size
    if size > MAX_BYTES:
        raise FileError(
            "too_large", f"{src} is {size} bytes; the limit is {MAX_BYTES}", src
        )
    return size


@dataclass
class Opened:
    """A file opened by someone else's runner (as_user.py), with what the
    store needs from it that would take reading it by path."""

    src: Path
    fd: int
    mime: str
    preview: str


def open_all(srcs: list[Path]) -> list[Opened]:
    """Every one of ``srcs`` checked, then opened: run on the runner of the
    user whose access decides, so the store can copy them as the server."""
    for src in srcs:
        check(src)
    out: list[Opened] = []
    try:
        for src in srcs:
            fd = os.open(src, os.O_RDONLY | os.O_CLOEXEC)
            out.append(Opened(src, fd, *classify(src)))
    except OSError as e:
        for o in out:
            os.close(o.fd)
        raise FileError("unreadable", f"cannot read {src}: {e}", src) from e
    return out


async def store_as(state_root: Path, srcs: list[Path], run: Runner) -> list[dict]:
    """``store_all``, with each file read with ``run``'s access and copied
    into the state root with the server's."""
    if run.user is None:
        return await asyncio.to_thread(store_all, state_root, srcs)
    opened = await run(open_all, srcs)
    return await asyncio.to_thread(store_all, state_root, srcs, opened)


def store_all(
    state_root: Path, srcs: list[Path], opened: list[Opened] | None = None
) -> list[dict]:
    """Copy every one of ``srcs``, or none: all are checked before the first
    copy, and a copy that fails anyway removes the ones made before it, so a
    failed send leaves nothing in the state root. ``opened`` are the same
    files already checked and opened (``open_all``); this closes them."""
    if opened is None:
        for src in srcs:
            check(src)
    done: list[dict] = []
    try:
        for i, src in enumerate(srcs):
            done.append(store(state_root, src, got=opened[i] if opened else None))
    except FileError:
        for rec in done:
            shutil.rmtree(state_root / "files" / rec["file_id"], ignore_errors=True)
        raise
    finally:
        for o in (opened or [])[len(done) :]:
            if o.fd >= 0:
                with contextlib.suppress(OSError):
                    os.close(o.fd)
    return done


def store(
    state_root: Path, src: Path, name: str | None = None, got: Opened | None = None
) -> dict:
    """Copy ``src`` under a fresh id, as ``name`` (its own name by default); the
    store record's body, without caption. With ``got``, from its descriptor,
    which this closes."""
    size = check(src) if got is None else os.fstat(got.fd).st_size
    name = name or src.name
    mime, preview = classify(src) if got is None else (got.mime, got.preview)
    file_id = secrets.token_urlsafe(16)
    root = state_root / "files"
    root.mkdir(parents=True, exist_ok=True)
    # Copied beside the id folders, under a name no id can match, then moved
    # in: a half-copied file is never at a URL, and any name, dotfiles
    # included, can be served.
    part = root / f".{file_id}.part"
    try:
        if got is None:
            shutil.copyfile(src, part)
        else:
            f = os.fdopen(got.fd, "rb")
            got.fd = -1  # f closes it now; store_all must not close it again
            with f, part.open("wb") as out:
                shutil.copyfileobj(f, out)
    except OSError as e:
        part.unlink(missing_ok=True)
        raise FileError("unreadable", f"cannot read {src}: {e}", src) from e
    folder = root / file_id
    folder.mkdir()
    dest = folder / name
    os.replace(part, dest)
    return {
        "kind": "file",
        "file_id": file_id,
        "name": name,
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
    return _policy(path.name, mime, preview, download)


def via_headers(name: str, download: bool) -> dict[str, str]:
    """The headers for a linked server's file streamed through ``/via``: decided
    here from its name, as for a file of this server's own. Nothing the far
    server sent is kept, because what it serves runs on this server's origin,
    next to the cookie that drives every agent here (web.py)."""
    mime, preview = classify_name(name)
    return _policy(name, mime, preview, download)


def _policy(name: str, mime: str, preview: str, download: bool) -> dict[str, str]:
    h = {
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Cache-Control": "private, max-age=31536000, immutable",
        "Content-Type": mime,
    }
    if download or preview == "other":
        h["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(name)}"
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
    return launch.detach([*cmd, str(path)], user=None)
