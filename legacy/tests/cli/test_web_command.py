"""`aegis web`: ensure a token and a daemon, then serve browsers."""

from pathlib import Path

from typer.testing import CliRunner

CONFIG = """default_agent: main
agents:
  main:
    provider: claude-code
    model: opus
"""


def _world(tmp_path, monkeypatch):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AEGIS_WEB_TOKEN", raising=False)
    calls: dict = {"ensure": [], "run": []}

    async def _fake_ensure(root, **kw):
        calls["ensure"].append(Path(root))
        return Path(root) / ".aegis" / "state" / "daemon.sock"

    async def _fake_run(app, *, bind, port):
        calls["run"].append((bind, port))

    monkeypatch.setattr("aegis.cli._ensure_daemon", _fake_ensure)
    monkeypatch.setattr("aegis.webterm.server.run_web", _fake_run)
    return calls


def test_web_ensures_a_daemon_then_serves(tmp_path, monkeypatch):
    from aegis.cli import app

    calls = _world(tmp_path, monkeypatch)
    r = CliRunner().invoke(app, ["web", "--no-browser"])
    assert r.exit_code == 0, r.output
    assert calls["ensure"] == [tmp_path.resolve()]
    assert calls["run"] and calls["run"][0][0] == "127.0.0.1"
    assert "/?t=" in r.output
    assert "port:" not in (tmp_path / ".aegis.yaml").read_text(), (
        "aegis web pinned its port into .aegis.yaml again"
    )


def test_web_refuses_a_broken_config_before_serving(tmp_path, monkeypatch):
    from aegis.cli import app

    calls = _world(tmp_path, monkeypatch)
    (tmp_path / ".aegis.yaml").write_text("agents: [not, a, mapping]\n")
    r = CliRunner().invoke(app, ["web", "--no-browser"])
    assert r.exit_code == 1
    assert calls["run"] == []
