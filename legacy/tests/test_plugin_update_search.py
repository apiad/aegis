"""`aegis plugin update` + `aegis plugin search`."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aegis.cli import app

runner = CliRunner()


def _setup_registry_with_plugin(reg: Path, *, version: str) -> None:
    plug = reg / "skill-system"
    if plug.exists():
        import shutil; shutil.rmtree(plug)
    plug.mkdir(parents=True)
    (plug / "plugin.toml").write_text(textwrap.dedent(f"""
        [plugin]
        name = "skill-system"
        version = "{version}"
        description = "A test plugin."
    """))
    (plug / "code.py").write_text(f"# version {version}\n")


def _setup_project(proj: Path, reg: Path) -> None:
    proj.mkdir()
    (proj / ".aegis").mkdir()
    (proj / ".aegis.yaml").write_text(textwrap.dedent(f"""
        plugin_registries:
          - file://{reg}
        agents: {{}}
    """))


def test_update_picks_up_new_version(tmp_path: Path, monkeypatch) -> None:
    reg = tmp_path / "registry"
    _setup_registry_with_plugin(reg, version="0.1.0")
    proj = tmp_path / "proj"
    _setup_project(proj, reg)
    monkeypatch.chdir(proj)

    runner.invoke(app, ["plugin", "install", "skill-system", "--yes"])
    _setup_registry_with_plugin(reg, version="0.2.0")
    r = runner.invoke(app, ["plugin", "update", "skill-system", "--yes"])
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["plugin", "list"])
    assert "0.2.0" in r.output


def test_update_refuses_on_local_edit(tmp_path: Path, monkeypatch) -> None:
    reg = tmp_path / "registry"
    _setup_registry_with_plugin(reg, version="0.1.0")
    proj = tmp_path / "proj"
    _setup_project(proj, reg)
    monkeypatch.chdir(proj)
    runner.invoke(app, ["plugin", "install", "skill-system", "--yes"])
    edited = proj / ".aegis/plugins/skill-system/code.py"
    edited.write_text("# locally edited\n")
    _setup_registry_with_plugin(reg, version="0.2.0")
    r = runner.invoke(app, ["plugin", "update", "skill-system", "--yes"])
    assert r.exit_code != 0
    assert "edited" in r.output.lower() or "diverged" in r.output.lower()


def test_search(tmp_path: Path, monkeypatch) -> None:
    reg = tmp_path / "registry"
    _setup_registry_with_plugin(reg, version="0.1.0")
    proj = tmp_path / "proj"
    _setup_project(proj, reg)
    monkeypatch.chdir(proj)
    r = runner.invoke(app, ["plugin", "search", "skill"])
    assert r.exit_code == 0
    assert "skill-system" in r.output
