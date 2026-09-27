"""`aegis server` runs the daemon; `aegis serve` still does, quietly.

The alias exists because `aegis bench --target 0.37.0` starts releases that
only know `serve`, and a unit written for `serve` must keep working until
stage 6 deploys the new ones.
"""

import re

from typer.testing import CliRunner

CONFIG = "default_agent: main\nagents:\n  main:\n    provider: claude-code\n    model: opus\n"


def _fake(monkeypatch, tmp_path):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    monkeypatch.chdir(tmp_path)
    runs = []

    async def _fake_serve(**kw):
        runs.append(kw)

    monkeypatch.setattr("aegis.cli._serve", _fake_serve)
    return runs


def test_server_and_its_old_name_both_run_the_daemon(tmp_path, monkeypatch):
    from aegis.cli import app

    runs = _fake(monkeypatch, tmp_path)
    for name in ("server", "serve"):
        r = CliRunner().invoke(app, [name])
        assert r.exit_code == 0, (name, r.output)
    assert len(runs) == 2 and all(kw["views"] for kw in runs)


def test_help_names_server_and_hides_serve():
    from aegis.cli import app

    out = CliRunner().invoke(app, ["--help"]).output
    # Match command rows, not prose: `--remote`'s help mentions "aegis serve."
    assert re.search(r"│ server\s", out), out
    assert not re.search(r"│ serve\s", out), "serve is listed in --help"
