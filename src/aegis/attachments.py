"""Files a person hands to a session: staged while they upload, moved into the
session's inbox when the message that carries them is sent.

A file uploads in chunks before its message exists, so it waits under
``<state>/inbox/<log_id>/.staged/<upload_id>/``, beside a ``.size`` file with
the size the browser declared. The agent never sees a staged file: the send
moves each one to ``<state>/inbox/<log_id>/<YYYYMMDD-HHMMSS>-<name>`` and only
then names it in the prompt. Every upload is checked before the first moves,
so a refused send moves nothing. Boot removes every staging folder, because no
browser's upload survives a restart.

The inbox is keyed by the log id, never the handle (DESIGN.md), and is mode
0700. Each sent file is also copied into the sent-files store (files.py), so
the person's row draws it with the card, the URLs and the sandbox a sent file
has, and a linked server's card loads it through /via. A copy, not a link:
the agent may edit its inbox file, and the person's row must keep showing
what the person sent.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import time
import unicodedata
from pathlib import Path

from . import files
from .files import FileError

CHUNK_BYTES = 256 * 1024
MAX_FILES = 20
NAME_BYTES = 120
STAGED = ".staged"
SIZE = ".size"
_UPLOAD_ID = re.compile(r"[A-Za-z0-9_-]{16,64}")


def inbox(state_root: Path, log_id: str) -> Path:
    return state_root / "inbox" / log_id


def ensure_inbox(state_root: Path, log_id: str) -> Path:
    """The session's inbox, created private if it is not there yet."""
    folder = inbox(state_root, log_id)
    folder.parent.mkdir(parents=True, exist_ok=True)
    folder.mkdir(mode=0o700, exist_ok=True)
    return folder


def clean_name(raw: str) -> str:
    """The last path component of what the browser called the file, with
    control characters dropped, a leading dot replaced, and at most
    NAME_BYTES bytes, the extension kept."""
    name = re.split(r"[/\\]", raw)[-1]
    # Control and format characters go; any other space becomes a plain one,
    # because an agent copying the path back types U+0020 (Android names a
    # voice note "Oct 10, 12.26\u202fPM.m4a").
    name = "".join(
        " " if unicodedata.category(c) == "Zs" else c
        for c in name
        if unicodedata.category(c)[0] != "C"
    ).strip()
    if not name:
        raise FileError("bad_name", f"{raw!r} names no file")
    if name.startswith("."):
        name = "_" + name[1:]
    while len(name.encode()) > NAME_BYTES:
        stem, ext = os.path.splitext(name)
        name = stem[:-1] + ext if stem else name[:-1]
    return name


def _staged(state_root: Path, log_id: str, upload_id: str) -> Path:
    folder = inbox(state_root, log_id) / STAGED / upload_id
    if not _UPLOAD_ID.fullmatch(upload_id) or not folder.is_dir():
        raise FileError(
            "no_upload",
            f"no upload {upload_id!r}: it was sent, removed, or the server restarted",
        )
    return folder


def _file(folder: Path) -> tuple[Path, int]:
    """The staged file and the size the browser declared for it."""
    declared = int((folder / SIZE).read_text())
    (path,) = [p for p in folder.iterdir() if p.name != SIZE]
    return path, declared


def begin(state_root: Path, log_id: str, name: str, size: int) -> str:
    """Stage an empty file for an upload of ``size`` bytes; its upload id."""
    if size > files.MAX_BYTES:
        raise FileError(
            "too_large", f"{name} is {size} bytes; the limit is {files.MAX_BYTES}"
        )
    clean = clean_name(name)
    staging = ensure_inbox(state_root, log_id) / STAGED
    staging.mkdir(mode=0o700, exist_ok=True)
    upload_id = secrets.token_urlsafe(16)
    folder = staging / upload_id
    folder.mkdir()
    (folder / SIZE).write_text(str(size))
    (folder / clean).touch()
    return upload_id


def put(state_root: Path, log_id: str, upload_id: str, offset: int, data: bytes) -> int:
    """Append ``data`` at ``offset``; the bytes now staged. A chunk the server
    already holds, resent after a reconnect, is acknowledged and not written
    again; a gap, an overlap or bytes past the declared size are refused."""
    path, declared = _file(_staged(state_root, log_id, upload_id))
    have = path.stat().st_size
    if offset + len(data) <= have:
        return have
    if offset != have:
        raise FileError(
            "bad_offset",
            f"{path.name} holds {have} bytes; a chunk at {offset} does not follow",
        )
    if have + len(data) > declared:
        raise FileError("too_large", f"{path.name} was declared as {declared} bytes")
    with path.open("ab") as f:
        f.write(data)
    return have + len(data)


def drop(state_root: Path, log_id: str, upload_id: str) -> None:
    shutil.rmtree(_staged(state_root, log_id, upload_id))


def _free(folder: Path, name: str) -> Path:
    """``folder / name``, or with -2, -3… before the extension if taken."""
    stem, ext = os.path.splitext(name)
    dest, n = folder / name, 1
    while dest.exists():
        n += 1
        dest = folder / f"{stem}-{n}{ext}"
    return dest


def commit(
    state_root: Path, log_id: str, upload_ids: list[str], now: float | None = None
) -> list[dict]:
    """Move every upload into the inbox and the files store, or none: each is
    checked complete before the first moves. One record per file, as
    files.store makes it without ``kind``, plus its inbox ``path``."""
    if len(upload_ids) > MAX_FILES:
        raise FileError(
            "too_many", f"{len(upload_ids)} files; a message carries {MAX_FILES}"
        )
    if len(set(upload_ids)) != len(upload_ids):
        raise FileError("duplicate_upload", "an upload is named twice")
    staged = []
    for upload_id in upload_ids:
        path, declared = _file(_staged(state_root, log_id, upload_id))
        have = path.stat().st_size
        if have != declared:
            raise FileError(
                "upload_incomplete", f"{path.name} has {have} of {declared} bytes"
            )
        staged.append(path)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    box = inbox(state_root, log_id)
    sent = []
    for path in staged:
        dest = _free(box, f"{stamp}-{path.name}")
        os.replace(path, dest)
        shutil.rmtree(path.parent)
        rec = files.store(state_root, dest, name=path.name)
        sent.append(
            {**{k: v for k, v in rec.items() if k != "kind"}, "path": str(dest)}
        )
    return sent


def message(typed: str, sent: list[dict]) -> str:
    """What the harness reads: the typed text, then one line per file. The
    block never starts with ``> from ``, which marks an inbox message."""
    if not sent:
        return typed
    lines = ["Attached files:"] + [
        f"- {f['path']} ({f['mime']}, {files.human_size(int(f['size']))})" for f in sent
    ]
    block = "\n".join(lines)
    return f"{typed}\n\n{block}" if typed.strip() else block


def clear_staged(state_root: Path) -> int:
    """Remove every session's staging folder; how many there were."""
    found = list((state_root / "inbox").glob(f"*/{STAGED}"))
    for folder in found:
        shutil.rmtree(folder, ignore_errors=True)
    return len(found)
