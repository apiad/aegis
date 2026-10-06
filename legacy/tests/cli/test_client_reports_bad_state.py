"""What `aegis` can still tell you once it is only a client.

Bare `aegis` no longer boots the brain; it spawns a daemon and pipes a
terminal to it. The daemon's stderr is /dev/null, so anything it dies of is
invisible. Whatever the client can check before spawning, it must, or the
failure reaches the user as a spawn timeout with no cause.

Two things it can check, and both used to be reported from the boot the
client no longer runs: the config, and the workspace snapshot.
"""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

from aegis.cli import app

runner = CliRunner()

@pytest.fixture(autouse=True)
def _never_spawn_a_daemon(monkeypatch, tmp_path):
    """Every test here asserts the client refuses BEFORE spawning.

    Without this the failing case starts a real detached daemon that
    outlives the test, which is how three of them ended up running on
    zion during one suite pass.
    """
    def _boom(*_a, **_kw):
        raise AssertionError("the client spawned a daemon instead of refusing")

    monkeypatch.setattr("aegis.daemon.lifecycle._spawn_detached", _boom)
    monkeypatch.setenv("AEGIS_DAEMON_DIR", str(tmp_path / "daemons"))


def test_a_broken_config_is_reported_before_any_daemon_is_spawned(
        tmp_path, monkeypatch):
    """The check that stays. A daemon spawned on an unparseable config dies
    with its reason in /dev/null, so the client must refuse first, and the
    autouse fixture above turns any spawn into a failure."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".aegis.yaml").write_text("- not a mapping\n")

    r = runner.invoke(app, [])

    assert r.exit_code == 1, r.output
    assert "must be a mapping" in r.output
