"""Where the Files view's deletes go: ``<state>/trash/``, for 30 days.

A file outside git has no other way back, so a delete moves the entry into
``<state>/trash/<YYYYMMDD-HHMMSS>-<8 hex>/item`` beside a ``manifest.json``
naming where it came from, and a restore moves it back unless something new
took its place. The trash lives under the state root, which fs_paths refuses,
so nothing in it can be read or written through the view. Entries older than
``KEEP_DAYS`` are purged when the server boots.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .ops import OpError

KEEP_DAYS = 30

_ID = re.compile(r"\d{8}-\d{6}-[0-9a-f]{8}")


def _trash(state: Path) -> Path:
    return state / "trash"


def count(path: Path) -> tuple[int, int]:
    """(files, bytes) under ``path``, or of the file itself. Symlinks count
    as files and are never followed."""
    if path.is_symlink() or not path.is_dir():
        return 1, path.lstat().st_size
    files = size = 0
    for root, _dirs, names in os.walk(path):
        for n in names:
            files += 1
            try:
                size += os.lstat(os.path.join(root, n)).st_size
            except OSError:
                pass
    return files, size


def delete(state: Path, path: Path, rel: str, who: str) -> dict:
    """Move ``path`` (``rel`` under the root) into the trash: its id, and the
    files and bytes it held."""
    now = datetime.now(UTC)
    tid = f"{now:%Y%m%d-%H%M%S}-{secrets.token_hex(4)}"
    box = _trash(state) / tid
    box.mkdir(parents=True)
    files, size = count(path)
    meta = {"path": rel, "at": now.isoformat(timespec="seconds"), "who": who}
    (box / "manifest.json").write_text(json.dumps({**meta, "files": files}))
    shutil.move(path, box / "item")
    return {"id": tid, "files": files, "size": size}


def manifest(state: Path, tid: str) -> dict:
    if not _ID.fullmatch(tid):
        raise OpError("not_found", f"no trash entry {tid!r}")
    try:
        return json.loads((_trash(state) / tid / "manifest.json").read_text())
    except (OSError, ValueError):
        raise OpError("not_found", f"no trash entry {tid}") from None


def restore(state: Path, tid: str, dest: Path) -> None:
    """Move entry ``tid`` back to ``dest``, which the caller resolved from the
    manifest's path; ``taken`` when something new is there."""
    box = _trash(state) / tid
    if dest.exists() or dest.is_symlink():
        raise OpError("taken", f"something new is at {manifest(state, tid)['path']}")
    shutil.move(box / "item", dest)
    shutil.rmtree(box)


def purge(state: Path, days: int = KEEP_DAYS, now: datetime | None = None) -> int:
    """Drop the entries deleted more than ``days`` ago: how many went."""
    trash = _trash(state)
    if not trash.is_dir():
        return 0
    cutoff = (now or datetime.now(UTC)) - timedelta(days=days)
    gone = 0
    for box in trash.iterdir():
        if not _ID.fullmatch(box.name):
            continue
        at = datetime.strptime(box.name[:15], "%Y%m%d-%H%M%S").replace(tzinfo=UTC)
        if at < cutoff:
            shutil.rmtree(box, ignore_errors=True)
            gone += 1
    return gone
