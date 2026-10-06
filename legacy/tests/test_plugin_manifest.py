"""plugin.toml parsing → PluginManifest."""
from __future__ import annotations

from pathlib import Path

import pytest

from aegis.plugins.manifest import ManifestError, PluginManifest, load_manifest


def _write(p: Path, body: str) -> Path:
    p.write_text(body)
    return p


def test_minimal(tmp_path: Path) -> None:
    f = _write(tmp_path / "plugin.toml", '[plugin]\nname = "x"\nversion = "0.1"\n')
    m = load_manifest(f)
    assert m.name == "x"
    assert m.version == "0.1"
    assert m.description == ""
    assert m.requires_aegis is None
    assert m.default_config == {}


def test_full(tmp_path: Path) -> None:
    body = """
[plugin]
name = "skill-system"
version = "0.1.0"
description = "Inject skills pre-turn."
requires_aegis = ">=0.15"

[default_config]
folder = ".aegis/skills/"
top_k = 3
"""
    f = _write(tmp_path / "plugin.toml", body)
    m = load_manifest(f)
    assert m.name == "skill-system"
    assert m.requires_aegis == ">=0.15"
    assert m.default_config["folder"] == ".aegis/skills/"
    assert m.default_config["top_k"] == 3


def test_missing_plugin_table(tmp_path: Path) -> None:
    f = _write(tmp_path / "plugin.toml", "[other]\nname = 'x'\n")
    with pytest.raises(ManifestError, match="\\[plugin\\]"):
        load_manifest(f)


def test_missing_required_field(tmp_path: Path) -> None:
    f = _write(tmp_path / "plugin.toml", '[plugin]\nname = "x"\n')
    with pytest.raises(ManifestError, match="version"):
        load_manifest(f)


def test_unknown_keys_preserved_in_raw(tmp_path: Path) -> None:
    body = """
[plugin]
name = "x"
version = "0.1"
future_field = "still here"
"""
    f = _write(tmp_path / "plugin.toml", body)
    m = load_manifest(f)
    assert m.raw["plugin"]["future_field"] == "still here"
