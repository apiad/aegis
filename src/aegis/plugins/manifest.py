"""plugin.toml parsing."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ManifestError(ValueError):
    """plugin.toml malformed."""


@dataclass(frozen=True)
class PluginManifest:
    name:           str
    version:        str
    description:    str = ""
    requires_aegis: str | None = None
    default_config: dict[str, Any] = field(default_factory=dict)
    raw:            dict[str, Any] = field(default_factory=dict)


def load_manifest(path: Path) -> PluginManifest:
    """Parse plugin.toml; fail loud on missing required fields."""
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"{path}: invalid TOML: {exc}") from exc
    if "plugin" not in raw or not isinstance(raw["plugin"], dict):
        raise ManifestError(f"{path}: missing [plugin] table")
    plug = raw["plugin"]
    for required in ("name", "version"):
        if required not in plug:
            raise ManifestError(f"{path}: [plugin].{required} required")
    return PluginManifest(
        name=plug["name"],
        version=plug["version"],
        description=plug.get("description", ""),
        requires_aegis=plug.get("requires_aegis"),
        default_config=raw.get("default_config", {}),
        raw=raw,
    )
