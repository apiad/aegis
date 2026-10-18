"""The index of one repository: its files' definitions and references, cached.

The files are what ``git ls-files`` lists with a known extension, so ignored and
untracked files are never indexed. The cache is one pickle per repository,
keyed by its path, holding each file's (mtime, size) and parse; a load re-parses
only the files whose stamp changed and rewrites the cache when any did.
"""

from __future__ import annotations

import gc
import hashlib
import os
import pickle
import re
import subprocess
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .parse import EXT, FAMILY, IMPLICIT_THIS, Def, FileInfo, Ref, parse_file

# Bump when Def, Ref or FileInfo change shape: an old cache is then ignored.
FORMAT = 3
MAX_BYTES = 1_000_000  # generated bundles and fixtures, not code to navigate
_VENDORED = ("vendor/", "node_modules/", "third_party/")


@dataclass
class Use:
    where: str  # the Def.id the use sits in, or the file for module level
    line: int
    sure: bool
    file: str


@dataclass
class Stats:
    files: int = 0
    parsed: int = 0
    seconds: float = 0.0
    failed: Counter = field(default_factory=Counter)  # language -> files skipped


def repo_root(start: Path) -> Path:
    out = subprocess.run(
        ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        raise ValueError(f"{start} is not inside a git repository")
    return Path(out.stdout.strip())


def cache_file(cache_dir: Path, root: Path) -> Path:
    key = hashlib.sha1(str(root).encode()).hexdigest()[:16]
    return cache_dir / f"{root.name}-{key}.v{FORMAT}.pkl"


class Index:
    def __init__(self, root: Path, cache_dir: Path):
        self.root = root.resolve()
        self.cache = cache_file(cache_dir, self.root)
        self.files: dict[str, tuple[tuple[int, int], FileInfo]] = {}
        self.stats = Stats()
        self._resolved: dict[tuple[str, str], tuple[list[Def], bool]] = {}
        self._refs_by_name: dict[str, list[tuple[str, Ref]]] | None = None
        self.load()

    def load(self) -> None:
        t0 = time.monotonic()
        old: dict = {}
        if self.cache.exists():
            gc.disable()  # a third off a load of hundreds of thousands of tuples
            try:
                old = pickle.loads(self.cache.read_bytes())
            except Exception:  # a truncated or foreign file: rebuild
                old = {}
            finally:
                gc.enable()
        out = subprocess.run(
            ["git", "-C", str(self.root), "ls-files", "-z"],
            capture_output=True,
            check=True,
        ).stdout
        for rel in out.decode(errors="replace").split("\0"):
            lang = EXT.get(os.path.splitext(rel)[1])
            if not lang or any("/" + v in "/" + rel for v in _VENDORED):
                continue
            try:
                st = (self.root / rel).stat()
            except (FileNotFoundError, NotADirectoryError):
                continue
            stamp = (st.st_mtime_ns, st.st_size)
            if rel in old and old[rel][0] == stamp:
                self.files[rel] = old[rel]
                continue
            if st.st_size > MAX_BYTES:
                continue
            try:
                info = parse_file(rel, (self.root / rel).read_bytes(), lang)
            except Exception:  # no grammar (offline), or a parser error
                self.stats.failed[lang] += 1
                continue
            self.files[rel] = (stamp, info)
            self.stats.parsed += 1
        if self.stats.parsed or len(self.files) != len(old):
            self.cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_bytes(pickle.dumps(self.files, protocol=pickle.HIGHEST_PROTOCOL))
            os.replace(tmp, self.cache)
        self.stats.files = len(self.files)
        self.stats.seconds = round(time.monotonic() - t0, 2)
        self.defs = {d.id: d for _, f in self.files.values() for d in f.defs}
        self.by_name: dict[str, list[Def]] = defaultdict(list)
        for d in self.defs.values():
            self.by_name[d.name].append(d)

    def info(self, rel: str) -> FileInfo:
        return self.files[rel][1]

    # resolution -------------------------------------------------------------

    def resolve(self, name: str, from_file: str) -> tuple[list[Def], bool]:
        """The definitions a name used in from_file may mean, and whether that is sure.

        Sure: one definition in the same file, or exactly one whose module and
        name both appear in the file's imports. Otherwise the narrowest set
        found, unsure."""
        key = (name, from_file)
        if key not in self._resolved:
            self._resolved[key] = self._resolve(name, from_file)
        return self._resolved[key]

    def _resolve(self, name: str, from_file: str) -> tuple[list[Def], bool]:
        lang = self.info(from_file).lang
        fam = FAMILY.get(lang, lang)
        cands = [
            d
            for d in self.by_name.get(name, ())
            if FAMILY.get(self.info(d.file).lang, self.info(d.file).lang) == fam
        ]
        if not cands:
            return [], False
        same = [d for d in cands if d.file == from_file]
        if same:
            return same, len(same) == 1
        imports = self.info(from_file).imports
        by_module = [d for d in cands if _module_named(d.file, imports)]
        named = re.search(rf"\b{re.escape(name)}\b", imports) is not None
        if named and len(by_module) == 1:
            return by_module, True
        return by_module or cands, False

    def symbol(self, s: str) -> list[Def]:
        """`name`, `Class.method`, `path:name` or a full id, to its definitions."""
        if s in self.defs:
            return [self.defs[s]]
        if ":" in s:
            f, q = s.split(":", 1)
            return [
                d
                for d in self.defs.values()
                if d.file.endswith(f)
                and (d.id.endswith(":" + q) or d.id.endswith("." + q))
            ]
        if "." in s:
            return [
                d
                for d in self.defs.values()
                if d.id.endswith(":" + s) or d.id.endswith("." + s)
            ]
        return list(self.by_name.get(s, ()))

    def _refs_named(self, name: str):
        if self._refs_by_name is None:
            self._refs_by_name = defaultdict(list)
            for rel, (_, f) in self.files.items():
                for r in f.refs:
                    self._refs_by_name[r[0]].append((rel, r))
        return self._refs_by_name.get(name, ())

    def uses(self, target: Def, calls_only: bool):
        """Every reference that may mean target, as a Use."""
        for rel, (name, line, is_call, enc, attr) in self._refs_named(target.name):
            if calls_only and not is_call:
                continue
            if target.kind == "method" and not attr:
                # A bare name is a method only from inside its own class, in
                # languages where `this.` is implicit.
                inside = enc and target.parent and enc.startswith(target.parent + ".")
                if not (self.info(rel).lang in IMPLICIT_THIS and inside):
                    continue
            cands, sure = self.resolve(name, rel)
            if target in cands:
                yield Use(enc or rel, line, sure, rel)

    def callees(self, d: Def):
        """(line, candidates, sure) for each call made inside d."""
        for name, line, is_call, enc, _ in self.info(d.file).refs:
            if enc == d.id and is_call:
                cands, sure = self.resolve(name, d.file)
                if cands:
                    yield line, cands, sure


def _module_named(file: str, imports: str) -> bool:
    p = Path(file)
    stem = p.parent.name if p.stem in ("__init__", "index", "mod") else p.stem
    return re.search(rf"\b{re.escape(stem)}\b", imports) is not None
