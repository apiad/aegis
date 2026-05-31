"""memory-system plugin: persistent memory + periodic dreaming.

Entries live as markdown files with YAML frontmatter under
<project>/.aegis/memory/entries/, indexed by .aegis/memory/MEMORY.md.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml


VALID_TYPES = ("user", "feedback", "fact", "reference")
MEMORY_SUBDIR = ".aegis/memory"
ENTRIES_SUBDIR = ".aegis/memory/entries"
DREAMS_SUBDIR = ".aegis/memory/dreams"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _kebab(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


@dataclass(frozen=True)
class Entry:
    slug:        str
    type:        str
    name:        str
    description: str
    created:     str
    updated:     str
    content:     str


def _entry_path(root: Path, slug: str) -> Path:
    return root / ENTRIES_SUBDIR / f"{slug}.md"


def _slug_for(type_: str, name: str) -> str:
    return f"{type_}_{_kebab(name)}"


def write_entry(root: Path, type_: str, name: str,
                description: str, content: str) -> Path:
    """Write a new entry. Fails if the slug already exists."""
    if type_ not in VALID_TYPES:
        raise ValueError(f"invalid type {type_!r}; expected one of {VALID_TYPES}")
    slug = _slug_for(type_, name)
    path = _entry_path(root, slug)
    if path.exists():
        raise FileExistsError(f"entry {slug!r} already exists at {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    now = _now_iso()
    front = {
        "type": type_,
        "name": _kebab(name),
        "description": description,
        "created": now,
        "updated": now,
    }
    body = (
        "---\n"
        + yaml.safe_dump(front, sort_keys=False, allow_unicode=True)
        + "---\n\n"
        + content.rstrip()
        + "\n"
    )
    path.write_text(body, encoding="utf-8")
    return path


def read_entry(root: Path, slug: str) -> Entry:
    """Read a single entry. Raises FileNotFoundError if missing,
    ValueError if frontmatter is malformed."""
    path = _entry_path(root, slug)
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"{path}: missing YAML frontmatter")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise ValueError(f"{path}: malformed frontmatter")
    front = yaml.safe_load(text[4:end]) or {}
    content = text[end + len("\n---\n"):].lstrip()
    return Entry(
        slug=slug,
        type=str(front.get("type", "")),
        name=str(front.get("name", "")),
        description=str(front.get("description", "")),
        created=str(front.get("created", "")),
        updated=str(front.get("updated", "")),
        content=content,
    )


def list_entries(root: Path) -> list[Entry]:
    """Return every well-formed entry under entries/, sorted by name."""
    folder = root / ENTRIES_SUBDIR
    if not folder.exists():
        return []
    out: list[Entry] = []
    for path in sorted(folder.glob("*.md")):
        slug = path.stem
        try:
            out.append(read_entry(root, slug))
        except (ValueError, FileNotFoundError):
            continue
    return out


def rebuild_index(root: Path) -> Path:
    """Rebuild MEMORY.md from the current state of entries/."""
    entries = list_entries(root)
    lines = ["# Memory index", "", "## Index", ""]
    for e in entries:
        lines.append(f"- [{e.name}](entries/{e.slug}.md) — {e.description}")
    path = root / MEMORY_SUBDIR / "MEMORY.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
