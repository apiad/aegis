from typer.testing import CliRunner
from aegis.cli import app


def test_aegis_token_prints_and_persists(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".aegis.yaml").write_text("agents: {}\n")  # minimal
    runner = CliRunner()
    r = runner.invoke(app, ["token"])
    assert r.exit_code == 0
    token = r.stdout.strip()
    assert len(token) >= 32                # secrets.token_urlsafe(32)
    # Idempotent — a second call returns the same token
    r2 = runner.invoke(app, ["token"])
    assert r2.stdout.strip() == token


def test_aegis_token_fails_without_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    r = runner.invoke(app, ["token"])
    assert r.exit_code != 0
    assert "No .aegis.yaml" in r.stdout or "No .aegis.yaml" in r.stderr
