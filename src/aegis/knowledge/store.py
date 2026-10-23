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
import shutil
from pathlib import Path

from aegis.knowledge import history
from aegis.knowledge.items import Item, check_name, dump, parse_memory, parse_skill
from aegis.ops import OpError

log = logging.getLogger("aegis.knowledge")


class Store:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._locks: dict[str, asyncio.Lock] = {}
        self._versions: dict[str, int] = {}
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

    def version(self, principal: str) -> int:
        return self._versions.get(principal, 0)

    def _changed(self, principal: str, item: Item) -> None:
        """A change through the store: bump the version and forget the cached
        parse, which a rewrite of the same size in the same clock tick would
        otherwise keep."""
        self._versions[principal] = self.version(principal) + 1
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
            parts = Path(rel).parts
            if (
                item.kind != "skill"
                or not parts
                or Path(rel).is_absolute()
                or (".." in parts or rel == "SKILL.md")
            ):
                raise OpError("bad_params", f"{rel!r} is not a file of the skill")
            files[rel] = data
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
        history.ensure_repo(base)
        if item.kind == "memory":
            path = base / "memories" / f"{item.name}.md"
        else:
            path = base / "skills" / item.name / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dump(item), encoding="utf-8")
        for rel, data in files.items():
            (path.parent / rel).parent.mkdir(parents=True, exist_ok=True)
            (path.parent / rel).write_bytes(data)
        return history.commit(
            base, [self._rel(item)], f"{verb} {item.kind} {item.name}", author
        )

    async def delete(self, principal: str, name: str, author: str) -> str:
        """Delete an item and commit; the commit's sha."""
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
        if item.kind == "memory":
            (base / "memories" / f"{item.name}.md").unlink()
        else:
            shutil.rmtree(base / "skills" / item.name)
        return history.commit(
            base, [self._rel(item)], f"delete {item.kind} {item.name}", author
        )
