"""A damaged workspace snapshot must not stop `aegis` from starting.

This file used to assert the opposite: exit 2, name the file, point at
`--clean`. That was right while `aegis` was the boot and could say so.
Once the boot moved into the daemon the exit reached nobody, because the
daemon writes its stderr to /dev/null, and refusing to start over a cache
of the tab roster costs more than the cache is worth. The transcripts are
in `sessions/` keyed by log_id and Ctrl+R still finds them.

The recovery itself, and the notice that follows it, are asserted against
a real boot in tests/test_workspace_quarantine.py. What is left here is
the CLI-level half: the client must not refuse.
"""
from typer.testing import CliRunner

from aegis.cli import app
from aegis.state.workspace import state_dir

runner = CliRunner()

_CONFIG = (
    "default_agent: default\n"
    "agents:\n"
    "  default:\n"
    "    provider: claude-code\n"
    "    model: opus\n"
)


def test_a_corrupt_snapshot_does_not_stop_the_client(tmp_path, monkeypatch):
    """Asserted by reaching the spawn. The client's job is to hand off to a
    daemon; getting that far is proof it did not refuse on the way."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AEGIS_DAEMON_DIR", str(tmp_path / "daemons"))
    (tmp_path / ".aegis.yaml").write_text(_CONFIG)
    sd = state_dir(tmp_path)
    sd.mkdir(parents=True)
    (sd / "workspace.json").write_text("{not json")

    reached = []

    class _Stop(Exception):
        """Stops the run at the spawn, so no real daemon is ever forked."""

    def _spawned(*_a, **_kw):
        reached.append(True)
        raise _Stop

    monkeypatch.setattr("aegis.daemon.lifecycle._spawn_detached", _spawned)

    result = runner.invoke(app, [])

    assert reached, (
        "the client refused instead of starting a daemon: "
        f"exit={result.exit_code} output={result.output!r}")
