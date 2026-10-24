"""The per-principal store: ``<state>/knowledge/<principal>/`` with
``memories/<name>.md`` and ``skills/<name>/SKILL.md``.

Parsed items are cached per principal, keyed by each file's path, mtime_ns
and size, so a scan stats every file and parses only the ones that changed,
whether through aegis or by hand; ``version`` moves on every change through
the store, so the service rescans at once after one. A file whose frontmatter
does not parse is skipped and logged once per path and mtime, so one bad hand
edit or import never hides the rest. Saves and deletes hold one
lock per principal, so two sessions saving at once both land as two commits;
the file write and the git commit run in a thread, off the event loop.
"""

from __future__ import annotations

import asyncio
import logging
import posixpath
import shutil
import subprocess
from pathlib import Path

from . import history
from .items import Item, check_name, dump, parse_memory, parse_skill
from ..ops import OpError

log = logging.getLogger("aegis.knowledge")


class Store:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._locks: dict[str, asyncio.Lock] = {}
        self._versions: dict[str, int] = {}
        self._item_versions: dict[tuple[str, str], int] = {}
        self._logged: set[tuple[str, float]] = set()
        # principal -> path -> ((mtime_ns, size), the item or None if bad)
        self._parsed: dict[str, dict[Path, tuple[tuple[int, int], Item | None]]] = {}
        self.parses = 0  # files parsed, for the tests

    def dir(self, principal: str) -> Path:
        if not principal or "/" in principal or principal.startswith("."):
            raise OpError("bad_params", f"no knowledge for {principal!r}")
        return self.root / principal

    def items(self, principal: str) -> list[Item]:
        """Every memory and skill of ``principal``, memories first, by name."""
        return self.scan(principal)[0]

    def scan(self, principal: str) -> tuple[list[Item], bool]:
        """``items``, and whether any file changed since the last scan. Stats
        every file; parses only new or changed ones. Blocking: the service
        runs it in a thread."""
        base = self.dir(principal)
        held = self._parsed.get(principal, {})
        fresh: dict[Path, tuple[tuple[int, int], Item | None]] = {}
        changed = False
        found = [
            (p, parse_memory, p.stem) for p in sorted((base / "memories").glob("*.md"))
        ] + [
            (p, parse_skill, p.parent.name)
            for p in sorted((base / "skills").glob("*/SKILL.md"))
        ]
        for p, parse, name in found:
            try:
                st = p.stat()
            except OSError:
                changed = True
                continue
            key = (st.st_mtime_ns, st.st_size)
            got = held.get(p)
            if got is None or got[0] != key:
                changed = True
                got = (key, self._read(p, parse, name))
            fresh[p] = got
        changed = changed or fresh.keys() != held.keys()
        self._parsed[principal] = fresh
        return [it for _, it in fresh.values() if it is not None], changed

    def _read(self, path: Path, parse, name: str) -> Item | None:
        self.parses += 1
        try:
            return parse(path.read_text(encoding="utf-8"), name, path)
        except (OSError, UnicodeDecodeError, ValueError) as e:
            try:
                key = (str(path), path.stat().st_mtime)
            except OSError:
                return None
            if key not in self._logged:
                self._logged.add(key)
                log.warning("knowledge: skipping %s: %s", path, e)
            return None

    def get(self, principal: str, name: str) -> Item | None:
        base = self.dir(principal)
        check_name(name)
        p = base / "memories" / f"{name}.md"
        if p.is_file():
            return self._read(p, parse_memory, name)
        p = base / "skills" / name / "SKILL.md"
        if p.is_file():
            return self._read(p, parse_skill, name)
        return None

    def _skill_dir(self, principal: str, name: str) -> Path:
        folder = self.dir(principal) / "skills" / check_name(name)
        if not (folder / "SKILL.md").is_file():
            raise OpError("not_found", f"no skill named {name!r}")
        return folder

    def files(self, principal: str, name: str) -> list[str]:
        """The files of a skill's folder, relative and sorted. A symlink is
        left out, since ``read_file`` would refuse it."""
        folder = self._skill_dir(principal, name)
        return sorted(
            p.relative_to(folder).as_posix()
            for p in folder.rglob("*")
            if p.is_file() and not p.is_symlink()
        )

    def read_file(self, principal: str, name: str, rel: str) -> bytes:
        """One file of a skill's folder. A path that leaves the folder, by
        ``..``, an absolute path or a symlink, is ``bad_params``."""
        folder = self._skill_dir(principal, name)
        norm = posixpath.normpath(rel) if rel else ""
        p = folder / norm
        if (
            not norm
            or posixpath.isabs(norm)
            or norm == ".."
            or norm.startswith("../")
            or any(q.is_symlink() for q in [p, *p.parents] if q.is_relative_to(folder))
        ):
            raise OpError("bad_params", f"{rel!r} is not a file of the skill")
        if not p.is_file():
            raise OpError("not_found", f"the skill {name!r} has no file {rel!r}")
        return p.read_bytes()

    def version(self, principal: str) -> int:
        return self._versions.get(principal, 0)

    def item_version(self, principal: str, name: str) -> int:
        """Moves on every save or delete of that one item through the store."""
        return self._item_versions.get((principal, name), 0)

    def _changed(self, principal: str, item: Item) -> None:
        """A change through the store: bump the version and forget the cached
        parse, which a rewrite of the same size in the same clock tick would
        otherwise keep."""
        self._versions[principal] = self.version(principal) + 1
        key = (principal, item.name)
        self._item_versions[key] = self._item_versions.get(key, 0) + 1
        f = self._path(self.dir(principal), item.kind, item.name)
        self._parsed.get(principal, {}).pop(
            f if item.kind == "memory" else f / "SKILL.md", None
        )

    def _lock(self, principal: str) -> asyncio.Lock:
        return self._locks.setdefault(principal, asyncio.Lock())

    def _path(self, base: Path, kind: str, name: str) -> Path:
        """A memory's file, or a skill's folder."""
        if kind == "memory":
            return base / "memories" / f"{name}.md"
        return base / "skills" / name

    def _rel(self, item: Item) -> str:
        if item.kind == "memory":
            return f"memories/{item.name}.md"
        return f"skills/{item.name}"

    async def save(
        self,
        principal: str,
        item: Item,
        author: str,
        extra: dict[str, bytes] | None = None,
    ) -> Item:
        """Write ``item`` (and, for a skill, ``extra`` files in its folder) and
        commit. Refused with ``name_taken`` when the other kind holds the
        name; an item of the same kind is replaced."""
        base = self.dir(principal)
        check_name(item.name)
        files = {}
        for rel, data in (extra or {}).items():
            norm = posixpath.normpath(rel)
            if (
                item.kind != "skill"
                or posixpath.isabs(norm)
                or norm in (".", "..", "SKILL.md")
                or norm.startswith("../")
            ):
                raise OpError("bad_params", f"{rel!r} is not a file of the skill")
            files[norm] = data
        async with self._lock(principal):
            other = "skill" if item.kind == "memory" else "memory"
            if self._path(base, other, item.name).exists():
                raise OpError("name_taken", f"{item.name!r} already names another item")
            held = self._path(base, item.kind, item.name).exists()
            verb = "update" if held else "save"
            await asyncio.to_thread(self._write, base, item, files, verb, author)
            self._changed(principal, item)
        return self.get(principal, item.name) or item

    def _write(
        self, base: Path, item: Item, files: dict, verb: str, author: str
    ) -> str:
        try:
            history.ensure_repo(base)
        except (OSError, subprocess.CalledProcessError) as e:
            raise _history_failed(e) from None
        if item.kind == "memory":
            path = base / "memories" / f"{item.name}.md"
        else:
            path = base / "skills" / item.name / "SKILL.md"
        # A replaced skill keeps only the extra files of the new save.
        stale = [
            p
            for p in (sorted(path.parent.rglob("*")) if item.kind == "skill" else [])
            if p.is_file()
            and p != path
            and p.relative_to(path.parent).as_posix() not in files
        ]
        before = _snapshot([path, *stale, *(path.parent / rel for rel in files)])
        try:
            for p in stale:
                p.unlink()
            _prune_dirs(path.parent, [p.parent for p in stale])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(dump(item), encoding="utf-8")
            for rel, data in files.items():
                (path.parent / rel).parent.mkdir(parents=True, exist_ok=True)
                (path.parent / rel).write_bytes(data)
            return history.commit(
                base, [self._rel(item)], f"{verb} {item.kind} {item.name}", author
            )
        except (OSError, subprocess.CalledProcessError) as e:
            _restore(base, before, [self._rel(item)])
            raise _history_failed(e) from None

    async def delete(self, principal: str, name: str, author: str) -> str:
        """Delete an item and commit; the commit's sha, "" when git never
        tracked it."""
        base = self.dir(principal)
        check_name(name)
        async with self._lock(principal):
            item = self.get(principal, name)
            if item is None:
                raise OpError("not_found", f"no item named {name!r}")
            sha = await asyncio.to_thread(self._remove, base, item, author)
            self._changed(principal, item)
        return sha

    def _remove(self, base: Path, item: Item, author: str) -> str:
        target = self._path(base, item.kind, item.name)
        held = [target] if item.kind == "memory" else sorted(target.rglob("*"))
        before = _snapshot([p for p in held if p.is_file()])
        try:
            if item.kind == "memory":
                target.unlink()
            else:
                shutil.rmtree(target)
            return history.commit(
                base, [self._rel(item)], f"delete {item.kind} {item.name}", author
            )
        except (OSError, subprocess.CalledProcessError) as e:
            _restore(base, before, [self._rel(item)])
            raise _history_failed(e) from None


def _snapshot(paths: list[Path]) -> dict[Path, bytes | None]:
    """Each path's bytes, None for one that does not exist yet."""
    return {p: p.read_bytes() if p.is_file() else None for p in paths}


def _restore(base: Path, before: dict[Path, bytes | None], rels: list[str]) -> None:
    """Put the files back as ``_snapshot`` found them, after a change git did
    not record, so the store and its history never drift apart."""
    for p, data in before.items():
        if data is None:
            p.unlink(missing_ok=True)
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
    _prune_dirs(base, [q.parent for q in before])
    history.unstage(base, rels)


def _prune_dirs(top: Path, dirs: list[Path]) -> None:
    """Remove each of ``dirs`` that is empty, and its empty parents, up to
    but not including ``top``."""
    for p in sorted(set(dirs), key=lambda q: -len(q.parts)):
        while (
            p != top and p.is_relative_to(top) and p.is_dir() and not any(p.iterdir())
        ):
            p.rmdir()
            p = p.parent


def _history_failed(e: Exception) -> OpError:
    why = (
        (e.stderr or "").strip() if isinstance(e, subprocess.CalledProcessError) else ""
    )
    return OpError(
        "history_failed",
        f"the change was not kept, since git could not record it ({why or e});"
        " the store is as it was",
    )
