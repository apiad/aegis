"""CLI-level smoke tests for the top-level `aegis` command."""
from typer.testing import CliRunner

from aegis.cli import app

runner = CliRunner()


def test_version_flag_prints_and_exits(tmp_path, monkeypatch):
    from importlib.metadata import version as _pkg_version
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == f"aegis {_pkg_version('aegis-harness')}"


def test_init_command_is_gone(tmp_path, monkeypatch):
    """`aegis init` no longer exists — replaced by `aegis config ...`
    and the empty-directory ConfigPanel bootstrap."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    # Typer exits non-zero on an unknown subcommand.
    assert result.exit_code != 0
