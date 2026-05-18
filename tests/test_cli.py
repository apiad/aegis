from typer.testing import CliRunner
from aegis.cli import app
from aegis.config import INIT_TEMPLATE

runner = CliRunner()


def test_init_creates_scaffold(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / ".aegis.py").read_text() == INIT_TEMPLATE


def test_init_refuses_overwrite(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".aegis.py").write_text("# existing\n")
    result = runner.invoke(app, ["init"])
    assert result.exit_code != 0
    assert "exists" in result.output


def test_run_without_config_points_to_init(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    result = runner.invoke(app, [])
    assert result.exit_code != 0
    assert "aegis init" in result.output


def test_version_flag_prints_and_exits(tmp_path, monkeypatch):
    # must work with NO .aegis.py and without launching the TUI
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == "aegis 0.1.0"
