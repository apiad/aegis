"""Read/write .aegis/plugins.lock."""
from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path
from typing import Any

import tomli_w


def lockfile_path(project_root: Path) -> Path:
    return project_root / ".aegis" / "plugins.lock"


def read_lock(project_root: Path) -> dict[str, Any]:
    p = lockfile_path(project_root)
    if not p.exists():
        return {"plugins": []}
    return tomllib.loads(p.read_text(encoding="utf-8"))


def write_lock(project_root: Path, data: dict[str, Any]) -> None:
    p = lockfile_path(project_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(tomli_w.dumps(data).encode("utf-8"))


def upsert(project_root: Path, entry: dict[str, Any]) -> None:
    data = read_lock(project_root)
    plugins = [p for p in data.get("plugins", []) if p.get("name") != entry["name"]]
    plugins.append(entry)
    plugins.sort(key=lambda p: p["name"])
    data["plugins"] = plugins
    write_lock(project_root, data)


def remove(project_root: Path, name: str) -> None:
    data = read_lock(project_root)
    data["plugins"] = [p for p in data.get("plugins", []) if p.get("name") != name]
    write_lock(project_root, data)


def hash_dir(path: Path) -> dict[str, str]:
    """Return {relpath: sha256} for every file under path."""
    out = {}
    for p in path.rglob("*"):
        if p.is_file():
            rel = str(p.relative_to(path))
            out[rel] = "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()
    return out
