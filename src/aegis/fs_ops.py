"""The Files view's operations: a person reads and writes the server's files.

Every path is relative to the config root and goes through
``fs_paths.resolve``. Every operation is ``local`` and none is ``agent``, so
Registry.call refuses an agent (it has its harness's own file tools) and a
caller acting through a link (a far server never reads or writes files here).
An admin and a member may both call them, and every write is logged with its
author in ``<state>/fs-log.jsonl``.

A save carries the hash of the text it was based on, and a file whose hash on
disk differs is not written: agents write the files a person edits, and
last-write-wins would silently drop an agent's edit made after the file was
opened. A write goes through a temporary file in the same folder and
``os.replace``, so a reader never sees half a file.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel

from . import files
from .fs_index import FsIndex
from .fs_paths import resolve
from .ops import Caller, NoParams, OpError

if TYPE_CHECKING:
    from .app import App

#: The most text a read sends, and the largest file a save writes. A bigger
#: text file opens read-only as its first TEXT_BYTES.
TEXT_BYTES = 2 * 1024 * 1024

# Kinds a file's name may give although the file is text: classify asks the
# system's mime table, which calls .rb "other" on some machines and .ts video.
_MAYBE_TEXT = ("other", "audio", "video")


class FsPath(BaseModel):
    model_config = {"extra": "forbid"}
    path: str


class FsWrite(BaseModel):
    model_config = {"extra": "forbid"}
    path: str
    content: str
    base: str | None


def _mtime(st: os.stat_result) -> int:
    return st.st_mtime_ns // 1_000_000


def _hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _decode(head: bytes, cut: bool) -> str | None:
    """``head`` as UTF-8, or None when it is not text. A cut head may end
    inside a character, which is still text."""
    if b"\x00" in head:
        return None
    try:
        return head.decode("utf-8")
    except UnicodeDecodeError as e:
        if cut and e.start >= len(head) - 3:
            return head[: e.start].decode("utf-8", "replace")
        return None


def _kind(path: Path) -> tuple[str, str]:
    mime, kind = files.classify(path)
    if kind in _MAYBE_TEXT and files._sniff_text(path):
        return "text/plain", "text"
    return mime, kind


def _read(path: Path) -> dict:
    st = path.stat()
    if not path.is_file():
        raise OpError("not_a_file", f"{path.name} is a folder")
    mime, kind = _kind(path)
    out = {
        "kind": kind,
        "mime": mime,
        "size": st.st_size,
        "mtime": _mtime(st),
        "hash": _hash(path),
    }
    texty = kind in ("text", "markdown") or path.suffix.lower() == ".svg"
    if texty and st.st_size <= files.MAX_BYTES:
        with path.open("rb") as f:
            head = f.read(TEXT_BYTES + 1)
        cut = len(head) > TEXT_BYTES
        text = _decode(head[:TEXT_BYTES], cut)
        if text is None:
            out.update(kind="other", mime="application/octet-stream")
        else:
            out["text"] = text
            if cut:
                out["cut"] = True
    return out


def _write(path: Path, data: bytes, mode: int | None) -> os.stat_result:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path.stat()


def _save(path: Path, rel: str, data: bytes, base: str | None):
    """Check ``base`` against the disk and write, in one step under
    register_fs_ops's lock: (whether the file existed, its new stat). A
    process outside aegis writing between the check and os.replace can still
    be overwritten; the window is a read and a rename, not a round trip."""
    exists = path.exists()
    if base is None and exists:
        raise OpError("exists", f"{rel} already exists")
    if base is not None and not exists:
        raise OpError("not_found", f"no file {rel}")
    mode = None
    if exists:
        if not path.is_file():
            raise OpError("not_a_file", f"{rel} is a folder")
        disk = _read(path)
        if disk["hash"] != base:
            raise OpError(
                "changed_on_disk",
                f"{rel} changed on disk since it was opened",
                # A cut text is not the file: Reload reads it again.
                {
                    "text": None if disk.get("cut") else disk.get("text"),
                    "hash": disk["hash"],
                },
            )
        # A buffer opened from the first TEXT_BYTES of a bigger file is not
        # the file: saving it would cut the file.
        if disk["size"] > TEXT_BYTES:
            raise OpError(
                "too_large", f"{rel} is over {TEXT_BYTES} bytes; it opens read-only"
            )
        mode = path.stat().st_mode & 0o7777
    return exists, _write(path, data, mode)


def register_fs_ops(app: App) -> FsIndex:
    r = app.registry
    root, state = app.roots.config_root, app.roots.state_root
    index = FsIndex(root, state)
    # One save at a time, so two saves on the same base cannot both pass the
    # hash check (_save).
    writing = asyncio.Lock()
    log_path = state / "fs-log.jsonl"

    def log(caller: Caller, op: str, path: str, size: int, to: str | None = None):
        line = {
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "who": r.who(caller).name,
            "op": op,
            "path": path,
            "to": to,
            "size": size,
        }
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line) + "\n")

    @r.op("fs.find", NoParams, local=True)
    async def find(p: NoParams, caller: Caller):
        """Every listed file under the root with its mtime in ms, newest first."""
        return {"files": [[rel, m] for rel, m in await index.entries()]}

    @r.op("fs.read", FsPath, local=True)
    async def read(p: FsPath, caller: Caller):
        """A file's kind, size, mtime and hash, and its text when it is text."""
        path = resolve(root, state, p.path)
        return {"path": p.path, **await asyncio.to_thread(_read, path)}

    @r.op("fs.stat", FsPath, local=True)
    async def stat(p: FsPath, caller: Caller):
        """A file's size, mtime and kind, polled for the open file."""
        path = resolve(root, state, p.path)
        st = path.stat()
        if not path.is_file():
            raise OpError("not_a_file", f"{p.path} is a folder")
        return {"size": st.st_size, "mtime": _mtime(st), "kind": _kind(path)[1]}

    @r.op("fs.write", FsWrite, local=True)
    async def write(p: FsWrite, caller: Caller):
        """Save a text file, only if it still has the hash ``base``; a null
        base creates it and refuses a file that exists."""
        data = p.content.encode("utf-8")
        if len(data) > TEXT_BYTES:
            raise OpError(
                "too_large", f"{len(data)} bytes; the editor saves {TEXT_BYTES}"
            )
        path = resolve(root, state, p.path, must_exist=False)
        async with writing:
            exists, st = await asyncio.to_thread(_save, path, p.path, data, p.base)
        log(caller, "write" if exists else "create", p.path, len(data))
        index.touched(p.path)
        return {"hash": hashlib.sha256(data).hexdigest(), "mtime": _mtime(st)}

    return index
