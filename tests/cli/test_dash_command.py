"""`aegis dash` is `aegis attach` with the fleet dashboard asked for in the
hello. The app lives in the daemon, so the client can only say it."""
import pytest
from typer.testing import CliRunner

from aegis import cli

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_DAEMON_DIR", str(tmp_path / "daemons"))


def test_dash_help_lists_the_attach_options():
    result = runner.invoke(cli.app, ["dash", "--help"])
    assert result.exit_code == 0, result.output
    assert "--view" in result.output
    assert "--cwd" in result.output


def test_dash_ensures_a_daemon_and_asks_for_the_fleet(tmp_path, monkeypatch):
    calls = {}

    async def _fake_ensure(root, **kw):
        calls["root"] = root
        return tmp_path / "d.sock"

    async def _fake_attach(path, view_id, **kw):
        calls["path"] = path
        calls["view_id"] = view_id
        calls["open"] = kw.get("open")

    monkeypatch.setattr(cli, "_ensure_daemon", _fake_ensure)
    monkeypatch.setattr(cli, "_attach", _fake_attach)
    result = runner.invoke(cli.app, ["dash", "--cwd", str(tmp_path),
                                     "--view", "wall"])
    assert result.exit_code == 0, result.output
    assert calls == {"root": tmp_path.resolve(), "path": tmp_path / "d.sock",
                     "view_id": "wall", "open": "fleet"}


def test_dash_defaults_to_this_terminals_view(tmp_path, monkeypatch):
    seen = {}

    async def _fake_ensure(root, **kw):
        return tmp_path / "d.sock"

    async def _fake_attach(path, view_id, **kw):
        seen["view_id"] = view_id

    monkeypatch.setattr(cli, "_ensure_daemon", _fake_ensure)
    monkeypatch.setattr(cli, "_attach", _fake_attach)
    monkeypatch.setattr(cli, "_tty_view_id", lambda: "tty-dev-pts-9")
    result = runner.invoke(cli.app, ["dash", "--cwd", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert seen["view_id"] == "tty-dev-pts-9"


def test_plain_attach_asks_for_nothing(tmp_path, monkeypatch):
    seen = {}

    async def _fake_ensure(root, **kw):
        return tmp_path / "d.sock"

    async def _fake_attach(path, view_id, **kw):
        seen["open"] = kw.get("open")

    monkeypatch.setattr(cli, "_ensure_daemon", _fake_ensure)
    monkeypatch.setattr(cli, "_attach", _fake_attach)
    result = runner.invoke(cli.app, ["attach", "--cwd", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert seen["open"] is None
