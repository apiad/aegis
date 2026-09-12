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
from aegis.state.workspace import state_dir

runner = CliRunner()

_GOOD_CONFIG = (
    "default_agent: default\n"
    "agents:\n"
    "  default:\n"
    "    provider: claude-code\n"
    "    model: opus\n"
)


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


def test_a_corrupt_workspace_is_named_before_any_daemon_is_spawned(
        tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".aegis.yaml").write_text(_GOOD_CONFIG)
    sd = state_dir(tmp_path)
    sd.mkdir(parents=True)
    (sd / "workspace.json").write_text("{not json")

    r = runner.invoke(app, [])

    assert r.exit_code == 2, r.output
    out = r.output + (r.stderr or "")
    assert "workspace.json" in out
    assert "--clean" in out, "the hint that gets the user past it is missing"


def test_clean_skips_the_workspace_check(tmp_path, monkeypatch):
    """`--clean` exists to ignore prior state, so the check it would trip
    over must not run. Asserted by the spawn guard firing: getting as far
    as a spawn attempt IS passing the check."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".aegis.yaml").write_text(_GOOD_CONFIG)
    sd = state_dir(tmp_path)
    sd.mkdir(parents=True)
    (sd / "workspace.json").write_text("{not json")

    r = runner.invoke(app, ["--clean"])

    out = r.output + (r.stderr or "")
    assert "workspace.json" not in out, \
        "--clean was refused by the check it is meant to skip"
    assert isinstance(r.exception, AssertionError), \
        f"expected to reach the spawn guard, got {r.exception!r}"
