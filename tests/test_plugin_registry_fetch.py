"""Fetch plugin folder from a registry URL."""
from __future__ import annotations

from pathlib import Path

import pytest

from aegis.plugins.registry import (
    RegistryURL, fetch_plugin, parse_registry_url,
)


def test_file_registry_fetch(tmp_path: Path) -> None:
    reg = tmp_path / "registry"
    plug = reg / "skill-system"
    plug.mkdir(parents=True)
    (plug / "plugin.toml").write_text('[plugin]\nname = "skill-system"\nversion = "0.1"\n')
    (plug / "code.py").write_text("# stub\n")
    url = parse_registry_url(f"file://{reg}")

    with fetch_plugin(url, plugin_name="skill-system") as fetched:
        assert (fetched / "plugin.toml").exists()
        assert (fetched / "code.py").exists()


def test_file_registry_missing_plugin(tmp_path: Path) -> None:
    reg = tmp_path / "registry"
    reg.mkdir()
    url = parse_registry_url(f"file://{reg}")
    with pytest.raises(FileNotFoundError, match="never"):
        with fetch_plugin(url, plugin_name="never"):
            pass


@pytest.mark.live
def test_gh_fetch_via_git_archive(tmp_path: Path) -> None:
    """Live: requires `git` on PATH and HTTPS access to github.com."""
    import shutil
    if shutil.which("git") is None:
        pytest.skip("git not on PATH")
    url = parse_registry_url("gh:apiad/aegis#plugins/")
    try:
        with fetch_plugin(url, plugin_name="skill-system") as fetched:
            assert (fetched / "plugin.toml").exists()
    except RuntimeError as exc:
        if "not found" in str(exc) or "exit" in str(exc):
            pytest.skip(f"skill-system not yet pushed to apiad/aegis: {exc}")
        raise
