"""The per-principal store: ``<state>/knowledge/<principal>/`` with
``memories/<name>.md`` and ``skills/<name>/SKILL.md``.

Files are read on each call; the search index is what is cached, keyed by
``version``, which every change through the store bumps. A file whose
frontmatter does not parse is skipped and logged once per path and mtime, so
one bad hand edit or import never hides the rest. Saves and deletes hold one
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

    def dir(self, principal: str) -> Path:
        if not principal or "/" in principal or principal.startswith("."):
            raise OpError("bad_params", f"no knowledge for {principal!r}")
        return self.root / principal

    def items(self, principal: str) -> list[Item]:
        """Every memory and skill of ``principal``, memories first, by name."""
        base = self.dir(principal)
        out: list[Item] = []
        for p in sorted((base / "memories").glob("*.md")):
            it = self._read(p, parse_memory, p.stem)
            if it is not None:
                out.append(it)
        for p in sorted((base / "skills").glob("*/SKILL.md")):
            it = self._read(p, parse_skill, p.parent.name)
            if it is not None:
                out.append(it)
        return out

    def _read(self, path: Path, parse, name: str) -> Item | None:
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
            self._versions[principal] = self.version(principal) + 1
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
            self._versions[principal] = self.version(principal) + 1
        return sha

    def _remove(self, base: Path, item: Item, author: str) -> str:
        if item.kind == "memory":
            (base / "memories" / f"{item.name}.md").unlink()
        else:
            shutil.rmtree(base / "skills" / item.name)
        return history.commit(
            base, [self._rel(item)], f"delete {item.kind} {item.name}", author
        )
