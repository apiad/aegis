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

Bytes a browser element fetches itself (an image, a PDF, a rendered HTML page,
a download) cannot carry the websocket's token, so they come from
``/fs/<ticket>/<name>`` (web.py): a ticket is 128 random bits mapped to a
relative path for ten minutes, in memory, so a restart ends them all. The route
resolves the path again on every request, so a ticket never reaches further
than ``resolve`` does at that moment. Each use renews the ten minutes, so a
playing video's range requests keep working; an unused ticket lapses.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import secrets
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

from pydantic import BaseModel

from . import launch
from . import files, fs_trash
from .fs_index import FsIndex, _is_repo, ignore_dir, ignore_file
from .fs_paths import resolve
from .ops import Caller, NoParams, OpError

if TYPE_CHECKING:
    from .app import App

#: The most text a read sends, and the largest file a save writes. A bigger
#: text file opens read-only as its first TEXT_BYTES.
TEXT_BYTES = 2 * 1024 * 1024

#: How long a ticket URL serves its file, in seconds.
TICKET_S = 600
_now = time.monotonic  # a ticket's clock; tests replace it

# Kinds a file's name may give although the file is text: classify asks the
# system's mime table, which calls .rb "other" on some machines and .ts video.
_MAYBE_TEXT = ("other", "audio", "video")


class FsPath(BaseModel):
    model_config = {"extra": "forbid"}
    path: str


class FsList(BaseModel):
    model_config = {"extra": "forbid"}
    dir: str = ""
    ignored: bool = False


class FsMove(BaseModel):
    model_config = {"extra": "forbid"}
    path: str
    to: str


class FsRestore(BaseModel):
    model_config = {"extra": "forbid"}
    trash_id: str


class FsWrite(BaseModel):
    model_config = {"extra": "forbid"}
    path: str
    content: str
    base: str | None


class Tickets:
    """Ticket to (relative path, expiry): what ``/fs/<ticket>/<name>`` serves."""

    def __init__(self) -> None:
        self._map: dict[str, tuple[str, float]] = {}

    def issue(self, rel: str) -> str:
        """A fresh ticket URL for ``rel``; expired tickets are dropped here."""
        now = _now()
        self._map = {t: v for t, v in self._map.items() if v[1] > now}
        ticket = secrets.token_urlsafe(16)
        self._map[ticket] = (rel, now + TICKET_S)
        return f"/fs/{ticket}/{quote(Path(rel).name)}"

    def find(self, ticket: str) -> str | None:
        """The path a live ticket names, or None. A use renews it: a video
        or a large PDF keeps asking for byte ranges as it plays or scrolls,
        long after the ticket was issued."""
        rel, expires = self._map.get(ticket, ("", 0.0))
        now = _now()
        if expires <= now:
            return None
        self._map[ticket] = (rel, now + TICKET_S)
        return rel


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
    # HTML is sent as text too, for its Source switch.
    texty = kind in ("text", "markdown", "html") or path.suffix.lower() == ".svg"
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


def _git_ignored(top: Path, paths: list[Path]) -> set[Path]:
    """Which of ``paths`` the repository at ``top`` ignores: one
    ``git check-ignore`` for the whole listing, nothing when git cannot say."""
    if not paths:
        return set()
    rels = [str(p.relative_to(top)) for p in paths]
    try:
        out = launch.run(
            ["git", "-C", str(top), "check-ignore", "--stdin", "-z"],
            input="\0".join(rels).encode("utf-8", "surrogateescape"),
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    if out.returncode not in (0, 1):  # 1: none ignored; 128: not a repository
        return set()
    said = out.stdout.decode("utf-8", "surrogateescape").split("\0")
    return {top / r for r in said if r}


def _repo_top(folder: Path, root: Path) -> Path | None:
    """The innermost repository holding ``folder``, up to ``root``. A
    worktree's ``.git`` file counts here: git answers for it."""
    root = root.resolve()  # ``folder`` is resolved: a symlinked root must match
    for p in (folder, *folder.parents):
        if (p / ".git").exists():
            return p
        if p == root:
            return None
    return None


def _holds_repo(path: Path) -> bool:
    """A repository, or a folder with one ``SEARCH_DEPTH`` levels down: the
    tree must reach it although git ignores the folder (``repos/``)."""
    if _is_repo(str(path)):
        return True
    try:
        return any(
            e.is_dir(follow_symlinks=False) and _is_repo(e.path)
            for e in os.scandir(path)
        )
    except OSError:
        return False


def _list(root: Path, state: Path, folder: Path, show_ignored: bool) -> list[dict]:
    real_state = state.resolve()
    entries = []
    for e in os.scandir(folder):
        path = Path(e.path)
        if e.name == ".git" or path.resolve() == real_state:
            continue
        try:
            st = e.stat()  # a symlink's target, as the editor would see it
            is_dir = e.is_dir()
        except OSError:  # a dangling symlink
            st, is_dir = e.stat(follow_symlinks=False), False
        fixed = ignore_dir(e.name) if is_dir else ignore_file(e.name)
        entries.append((path, is_dir, st, fixed))
    top = _repo_top(folder, root)
    by_git = (
        _git_ignored(top, [p for p, _, _, fixed in entries if not fixed])
        if top
        else set()
    )
    out = []
    for path, is_dir, st, fixed in entries:
        repo = is_dir and _is_repo(str(path))
        # A repository answers for itself, not to the one around it.
        ignored = not repo and (fixed or path in by_git)
        if ignored and not show_ignored and not (is_dir and _holds_repo(path)):
            continue
        out.append(
            {
                "name": path.name,
                "kind": "dir" if is_dir else "file",
                "size": None if is_dir else st.st_size,
                "mtime": _mtime(st),
                "ignored": ignored,
                "repo": repo,
            }
        )
    out.sort(key=lambda d: (d["kind"] != "dir", d["name"].lower(), d["name"]))
    return out


def register_fs_ops(app: App) -> tuple[FsIndex, Tickets]:
    r = app.registry
    root, state = app.roots.config_root, app.roots.state_root
    index = FsIndex(root, state)
    tickets = Tickets()
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
        """A file's kind, size, mtime and hash, its text when it is text, and
        a ticket URL for every kind a browser element draws itself."""
        path = resolve(root, state, p.path)
        out = {"path": p.path, **await asyncio.to_thread(_read, path)}
        if out["kind"] not in ("text", "markdown") or "text" not in out:
            out["url"] = tickets.issue(p.path)
        return out

    @r.op("fs.ticket", FsPath, local=True)
    async def ticket(p: FsPath, caller: Caller):
        """A ticket URL for one file, such as an image a Markdown file links to."""
        path = resolve(root, state, p.path)
        if not path.is_file():
            raise OpError("not_a_file", f"{p.path} is a folder")
        return {"url": tickets.issue(p.path)}

    @r.op("fs.open", FsPath, local=True)
    async def open_(p: FsPath, caller: Caller):
        """Open a file in the desktop's app for it, as file.open does for a
        sent file: only from a browser on the server's desktop."""
        if not caller.desktop:
            raise OpError(
                "not_local", "only a browser on the server's desktop can do this"
            )
        path = resolve(root, state, p.path)
        try:
            files.open_natively(path)
        except (files.FileError, OSError) as e:
            raise OpError("open_failed", str(e)) from e

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

    def entry(rel: str) -> Path:
        """The entry ``rel`` names itself, a symlink rather than its target
        (a dangling one included), once ``resolve`` has passed both. Never
        the state root's folder or one above it, the root included: moving
        ``.aegis`` would carry the admin token out to where fs.read reaches."""
        real = resolve(root, state, rel, must_exist=False)
        if state.resolve().is_relative_to(real):
            raise OpError("forbidden", f"{rel} holds aegis's own state")
        path = resolve(root, state, str(Path(rel).parent)) / Path(rel).name
        if not (path.exists() or path.is_symlink()):
            raise OpError("not_found", f"no file {rel}")
        return path

    @r.op("fs.list", FsList, local=True)
    async def list_(p: FsList, caller: Caller):
        """One folder's entries, folders first, each alphabetical; ignored
        ones only when asked for, except a folder holding a repository."""
        folder = resolve(root, state, p.dir) if p.dir else root.resolve()
        if not folder.is_dir():
            raise OpError("not_a_folder", f"{p.dir} is a file")
        entries = await asyncio.to_thread(_list, root, state, folder, p.ignored)
        return {"dir": p.dir, "entries": entries}

    @r.op("fs.mkdir", FsPath, local=True)
    async def mkdir(p: FsPath, caller: Caller):
        """Create a folder; refuses a name that exists."""
        path = resolve(root, state, p.path, must_exist=False)
        async with writing:
            if path.exists() or path.is_symlink():
                raise OpError("exists", f"{p.path} already exists")
            path.mkdir()
        log(caller, "mkdir", p.path, 0)
        return {"path": p.path}

    @r.op("fs.move", FsMove, local=True)
    async def move(p: FsMove, caller: Caller):
        """Rename or move a file or folder; refuses a target that exists."""
        src = entry(p.path)
        dst = resolve(root, state, p.to, must_exist=False)
        if dst.is_relative_to(src):
            raise OpError("forbidden", f"{p.to} is inside {p.path}")
        async with writing:
            if dst.exists() or dst.is_symlink():
                raise OpError("exists", f"{p.to} already exists")
            size = await asyncio.to_thread(lambda: fs_trash.count(src)[1])
            await asyncio.to_thread(shutil.move, src, dst)
        log(caller, "move", p.path, size, p.to)
        index.removed(p.path)
        index.added(p.to)
        return {"path": p.to}

    @r.op("fs.count", FsPath, local=True)
    async def count(p: FsPath, caller: Caller):
        """How many files and bytes a file or folder holds, every file
        counted, ignored ones included: what a delete would take."""
        path = entry(p.path)
        files, size = await asyncio.to_thread(fs_trash.count, path)
        return {"files": files, "size": size}

    @r.op("fs.delete", FsPath, local=True)
    async def delete(p: FsPath, caller: Caller):
        """Move a file or folder into the trash: its id there and how many
        files it held."""
        path = entry(p.path)
        who = r.who(caller).name
        async with writing:
            t = await asyncio.to_thread(fs_trash.delete, state, path, p.path, who)
        log(caller, "delete", p.path, t["size"], t["id"])
        index.removed(p.path)
        return {"trash_id": t["id"], "files": t["files"]}

    @r.op("fs.restore", FsRestore, local=True)
    async def restore(p: FsRestore, caller: Caller):
        """Put a deleted entry back, or ``taken`` when something new is there."""
        rel = fs_trash.manifest(state, p.trash_id)["path"]
        dest = resolve(root, state, rel, must_exist=False)
        async with writing:
            await asyncio.to_thread(fs_trash.restore, state, p.trash_id, dest)
        size = await asyncio.to_thread(lambda: fs_trash.count(dest)[1])
        log(caller, "restore", rel, size, p.trash_id)
        index.added(rel)
        return {"path": rel}

    return index, tickets
