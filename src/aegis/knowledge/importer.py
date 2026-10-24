"""Reading what /setup moves into the store: a harness's skill folder, a
memory file, or a procedure doc with a ``when:`` line.

Everything here blocks and reads only the source: the op runs it on the
session user's runner (as_user.py), so in multi-user mode the kernel checks
each read against the member, never the server. A skill folder is read whole,
scripts and binaries included, up to ``MAX_FOLDER_BYTES``. A symlink that
leaves the folder is refused, since following it would copy a file the
person never ticked; one that stays inside is read as the file it names.
``.git`` and ``__pycache__`` are left behind: they are a checkout's and an
interpreter's, not the skill's.

Nothing here writes or deletes: removing an original is a row of its own,
which the agent applies after the import.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .items import FRONT, Item, check_name, parse_memory, parse_skill, _split
from ..ops import OpError

MAX_FOLDER_BYTES = 20 * 2**20
SKIP_DIRS = {".git", "__pycache__"}


def _refuse(source: Path, why: str) -> OpError:
    return OpError("bad_source", f"{source}: {why}")


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise _refuse(path, "is not UTF-8 text") from None


def _inside(path: Path, top: Path) -> bool:
    try:
        return Path(os.path.realpath(path)).is_relative_to(top)
    except OSError:
        return False


def skill_folder(source: Path) -> tuple[str, dict[str, bytes]]:
    """A skill folder's SKILL.md text and its other files by relative path."""
    if not source.is_dir():
        raise _refuse(source, "is not a folder")
    top = Path(os.path.realpath(source))
    files: dict[str, bytes] = {}
    total = 0
    for root, dirs, names in os.walk(source):
        here = Path(root)
        keep = []
        for d in sorted(dirs):
            if d in SKIP_DIRS:
                continue
            if (here / d).is_symlink():
                if not _inside(here / d, top):
                    raise _refuse(here / d, "is a link that leaves the skill's folder")
                continue  # its target is inside, and is walked there
            keep.append(d)
        dirs[:] = keep
        for n in sorted(names):
            p = here / n
            if p.is_symlink() and not _inside(p, top):
                raise _refuse(p, "is a link that leaves the skill's folder")
            if not p.is_file():
                continue  # a socket, a fifo, a dangling link
            total += p.stat().st_size
            if total > MAX_FOLDER_BYTES:
                raise OpError(
                    "too_large",
                    f"{source} holds more than {MAX_FOLDER_BYTES} bytes; a skill "
                    "holds at most that",
                )
            files[p.relative_to(source).as_posix()] = p.read_bytes()
    if "SKILL.md" not in files:
        raise _refuse(source, "has no SKILL.md")
    try:
        text = files.pop("SKILL.md").decode("utf-8")
    except UnicodeDecodeError:
        raise _refuse(source / "SKILL.md", "is not UTF-8 text") from None
    return text, files


def _stamp(v: object) -> object:
    """A date or time the YAML read, as text the store writes back alike."""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%dT%H:%M:%SZ")
    return v


def _described(item: Item, source: Path) -> Item:
    """``item`` with a description: its own, else its body's first line."""
    if item.description:
        return item
    line = next(
        (ln.strip("# ").strip() for ln in item.body.splitlines() if ln.strip()), ""
    )
    if not line:
        raise _refuse(source, "has neither a description nor any text")
    return Item(item.kind, item.name, line[:200], item.body, item.meta)


def load(
    kind: str, source: Path, name: str | None, expires: date | None, now: str
) -> tuple[Item, dict[str, bytes]]:
    """The item ``source`` becomes, saved approved, and a skill's other
    files. ``name`` defaults to the folder's or file's own name; ``expires``
    makes a memory temporary."""
    if not source.is_absolute():
        raise _refuse(source, "is not an absolute path")
    if source.is_symlink():
        raise _refuse(source, "is a link; tick the file or folder it names")
    stem = source.name if kind == "skill" else source.stem
    name = check_name(name if name is not None else stem)
    try:
        if kind == "skill":
            text, files = skill_folder(source)
            got = parse_skill(text, name)
            meta = {k: v for k, v in got.meta.items() if k != "status"}
            return _described(
                Item("skill", name, got.description, got.body, meta), source
            ), files
        if not source.is_file():
            raise _refuse(source, "is not a file")
        text = _text(source)
        if kind == "doc":
            # A procedure doc: ``when:`` says when to use it, which is what a
            # skill's description says.
            data, body = _split(text) if FRONT.match(text) else ({}, text)
            when = str(data.pop("when", "") or data.pop("description", "")).strip()
            if not when:
                raise _refuse(source, "has no when: line to describe it")
            data.pop("name", None)
            data.pop("status", None)
            return _described(Item("skill", name, when, body, data), source), {}
        got = parse_memory(text, name)
    except ValueError as e:
        raise _refuse(source, str(e)) from None
    # Claude Code's auto-memory keeps type under metadata: in newer files.
    meta: dict[str, Any] = {k: _stamp(v) for k, v in got.meta.items()}
    nested: Any = meta.get("metadata")
    if "type" not in meta and isinstance(nested, dict) and nested.get("type"):
        meta["type"] = nested["type"]
    if isinstance(nested, dict) and set(nested) <= {"type"}:
        meta.pop("metadata")
    if meta.get("type") not in ("user", "feedback", "project", "reference"):
        meta["type"] = "reference"
    expires = expires or got.expires
    meta.update(
        status="approved",
        lifetime="temporary" if expires else "permanent",
        expires=expires,
        created=meta.get("created") or now,
        source="import",
        imported_from=str(source),
    )
    meta = {k: v for k, v in meta.items() if v is not None}
    return _described(Item("memory", name, got.description, got.body, meta), source), {}
