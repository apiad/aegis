"""One item of knowledge: a memory file or a skill's SKILL.md.

Both open with a YAML frontmatter block between ``---`` lines, which carries
``name`` and ``description`` and, for a memory, ``type``, ``lifetime``,
``expires``, ``status``, ``paths``, ``created`` and ``source``. A skill keeps
whatever else its frontmatter holds (an ``aegis:`` block, keys other harnesses
read) in ``meta``, so a skill copied out of aegis still works elsewhere.

A block that does not parse raises ``ValueError``: the store skips that file
and keeps serving the rest.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from ruamel.yaml import YAML, YAMLError

from aegis.ops import OpError

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
FRONT = re.compile(r"\A---\n(.*?)\n?---\n?", re.S)

Kind = Literal["skill", "memory"]


def check_name(name: str) -> str:
    """``name`` when it is a safe item name, else ``bad_name``. A name is a
    file stem, so it can never hold a path separator or a capital."""
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise OpError(
            "bad_name",
            f"{name!r} is not an item name: lowercase letters, digits, - and _,"
            " starting with a letter or digit, at most 64 characters",
        )
    return name


@dataclass(frozen=True)
class Item:
    kind: Kind
    name: str
    description: str
    body: str
    meta: dict[str, Any] = field(default_factory=dict)
    path: Path | None = None

    @property
    def status(self) -> str:
        return str(self.meta.get("status") or "approved")

    @property
    def lifetime(self) -> str:
        return str(self.meta.get("lifetime") or "permanent")

    @property
    def expires(self) -> date | None:
        v = self.meta.get("expires")
        if isinstance(v, datetime):  # YAML reads 2026-12-01T00:00:00Z as one
            return v.date()
        if isinstance(v, date):
            return v
        if isinstance(v, str) and v:
            try:
                return datetime.fromisoformat(v).date()
            except ValueError:
                return None
        return None

    @property
    def paths(self) -> list[str]:
        v = self.meta.get("paths") or []
        return [str(p) for p in v] if isinstance(v, list) else [str(v)]

    def expired(self, today: date | None = None) -> bool:
        e = self.expires
        return e is not None and e < (today or date.today())


def _yaml() -> YAML:
    y = YAML(typ="safe", pure=True)
    y.default_flow_style = None
    y.allow_unicode = True
    y.width = 4096
    y.sort_base_mapping_type_on_output = False  # name and description first
    return y


def _split(text: str) -> tuple[dict[str, Any], str]:
    m = FRONT.match(text)
    if m is None:
        raise ValueError("no frontmatter (--- name, description ---)")
    try:
        data = _yaml().load(m.group(1)) or {}
    except YAMLError as e:
        raise ValueError(f"the frontmatter does not parse: {e}") from None
    if not isinstance(data, dict):
        raise ValueError("the frontmatter is not a mapping of keys")
    return data, text[m.end() :]


def _parse(kind: Kind, text: str, name: str, path: Path | None) -> Item:
    data, body = _split(text)
    data.pop("name", None)
    description = data.pop("description", None) or ""
    return Item(kind, name, str(description).strip(), body, data, path)


def parse_memory(text: str, name: str, path: Path | None = None) -> Item:
    """A memory from its file's text. The name is the file's stem, whatever
    the frontmatter says."""
    return _parse("memory", text, name, path)


def parse_skill(skill_md: str, name: str, path: Path | None = None) -> Item:
    """A skill from its SKILL.md. The name is its folder's."""
    return _parse("skill", skill_md, name, path)


def dump(item: Item) -> str:
    """The file's text: frontmatter (name and description first) and body."""
    buf = io.StringIO()
    _yaml().dump({"name": item.name, "description": item.description, **item.meta}, buf)
    return f"---\n{buf.getvalue()}---\n{item.body}"


dump_memory = dump
