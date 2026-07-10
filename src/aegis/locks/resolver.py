from __future__ import annotations

from pathlib import Path

_GLOB_CHARS = "*?["


def resolve_paths(paths: list[str],
                  root: Path) -> tuple[frozenset[str], frozenset[str]]:
    prefixes: set[str] = set()
    files: set[str] = set()
    for raw in paths:
        p = raw.strip()
        if not p:
            continue
        if any(ch in p for ch in _GLOB_CHARS):
            for m in root.glob(p):
                rel = m.relative_to(root).as_posix()
                if m.is_dir():
                    prefixes.add(rel + "/")
                else:
                    files.add(rel)
            continue
        if p.endswith("/"):
            prefixes.add(p)
        else:
            files.add(p)
    return frozenset(prefixes), frozenset(files)
