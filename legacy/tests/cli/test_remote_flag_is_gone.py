"""`aegis --remote` is gone, and the commands beside it are not.

The flag carried a second, semantic protocol between the UI and the brain.
It rotted — eighteen subsystems never got a field — and the daemon's byte
protocol replaced it. A reader finding `--remote` in a release note should
find nothing in the CLI.
"""

import re

from typer.testing import CliRunner


def test_the_remote_flags_are_gone():
    from aegis.cli import app

    out = CliRunner().invoke(app, ["--help"]).output
    for flag in ("--remote", "--token", "--tail"):
        assert flag not in out, f"{flag} is still offered"


def test_passing_remote_is_an_error_not_a_silent_no_op():
    from aegis.cli import app

    r = CliRunner().invoke(app, ["--remote", "ws://localhost:8080"])
    assert r.exit_code != 0, "aegis accepted a flag it no longer implements"


def test_the_daemon_commands_survive():
    """Review Focus 4: the hidden `serve` alias is what `aegis bench
    --target <old>` and any unit written before the rename still call."""
    from aegis.cli import app

    out = CliRunner().invoke(app, ["--help"]).output
    assert re.search(r"│ server\s", out), out
    assert re.search(r"│ web\s", out), out
    assert not re.search(r"│ serve\s", out), "serve should stay hidden, not listed"
    assert CliRunner().invoke(app, ["serve", "--help"]).exit_code == 0, (
        "the hidden serve alias stopped resolving"
    )
