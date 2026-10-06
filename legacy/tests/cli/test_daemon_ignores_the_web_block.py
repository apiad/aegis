"""A terminal `aegis` must never start a web server.

It did whenever `.aegis.yaml` carried a token-bearing `web:` block, which
`aegis web` wrote on first use: every later daemon for that root bound the
port, and on 2026-09-13 four of them collided on it.
"""

from typer.testing import CliRunner

CONFIG = """default_agent: main
agents:
  main:
    provider: claude-code
    model: opus
web:
  token: abc
  port: 8931
"""


def test_the_daemon_is_not_handed_the_web_block(tmp_path, monkeypatch):
    from aegis.cli import app

    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    monkeypatch.chdir(tmp_path)
    seen: dict = {}

    async def _fake_serve(**kw):
        seen.update(kw)

    monkeypatch.setattr("aegis.cli._serve", _fake_serve)
    r = CliRunner().invoke(app, ["serve"])
    assert r.exit_code == 0, r.output
    assert seen, "serve did not reach _serve"
    assert "web" not in seen, "the daemon was still handed the web block"
