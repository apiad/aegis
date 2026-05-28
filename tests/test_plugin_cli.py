"""`aegis plugin` CLI surface — install/uninstall/list/show against local source."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aegis.cli import app

runner = CliRunner()


def _make_src(tmp_path: Path) -> Path:
    src = tmp_path / "skill-system"
    src.mkdir()
    (src / "plugin.toml").write_text(
        '[plugin]\nname = "skill-system"\nversion = "0.1.0"\n'
        'description = "test"\n'
    )
    (src / "code.py").write_text("# stub\n")
    return src


def test_install_then_list(tmp_path: Path, monkeypatch) -> None:
    src = _make_src(tmp_path)
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".aegis").mkdir()
    (proj / ".aegis.yaml").write_text("agents: {}\n")
    monkeypatch.chdir(proj)
    r = runner.invoke(app, [
        "plugin", "install", "skill-system", "--from", str(src), "--yes",
    ])
    assert r.exit_code == 0, r.output
    assert "skill-system" in r.output

    r = runner.invoke(app, ["plugin", "list"])
    assert r.exit_code == 0
    assert "skill-system" in r.output
    assert "0.1.0" in r.output


def test_show(tmp_path: Path, monkeypatch) -> None:
    src = _make_src(tmp_path)
    proj = tmp_path / "proj"; proj.mkdir()
    (proj / ".aegis").mkdir(); (proj / ".aegis.yaml").write_text("agents: {}\n")
    monkeypatch.chdir(proj)
    runner.invoke(app, ["plugin", "install", "skill-system", "--from", str(src), "--yes"])
    r = runner.invoke(app, ["plugin", "show", "skill-system"])
    assert r.exit_code == 0
    assert "skill-system" in r.output
    assert "0.1.0" in r.output


def test_uninstall(tmp_path: Path, monkeypatch) -> None:
    src = _make_src(tmp_path)
    proj = tmp_path / "proj"; proj.mkdir()
    (proj / ".aegis").mkdir(); (proj / ".aegis.yaml").write_text("agents: {}\n")
    monkeypatch.chdir(proj)
    runner.invoke(app, ["plugin", "install", "skill-system", "--from", str(src), "--yes"])
    r = runner.invoke(app, ["plugin", "uninstall", "skill-system", "--yes"])
    assert r.exit_code == 0
    r = runner.invoke(app, ["plugin", "list"])
    assert "skill-system" not in r.output


def test_install_resolves_against_file_registry(tmp_path: Path, monkeypatch) -> None:
    reg = tmp_path / "registry"
    plug = reg / "skill-system"
    plug.mkdir(parents=True)
    (plug / "plugin.toml").write_text(
        '[plugin]\nname = "skill-system"\nversion = "0.1.0"\n'
    )
    (plug / "code.py").write_text("# stub\n")

    proj = tmp_path / "proj"; proj.mkdir()
    (proj / ".aegis").mkdir()
    (proj / ".aegis.yaml").write_text(textwrap.dedent(f"""
        plugin_registries:
          - file://{reg}
        agents: {{}}
    """))
    monkeypatch.chdir(proj)

    r = runner.invoke(app, ["plugin", "install", "skill-system", "--yes"])
    assert r.exit_code == 0, r.output
    assert (proj / ".aegis" / "plugins" / "skill-system" / "plugin.toml").exists()
