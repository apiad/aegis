"""A error message must reach the user in one piece.

`Console.print` wraps at the console width, so a long enough path pushed
the text across a line break: "…: top \nlevel must be a mapping". Legible
to a human squinting at it, but no longer one line to grep, and it turned
`test_cli_still_exits_1_on_bad_config` into a test that passed or failed on
how long pytest's temp directory happened to be that run. It had been
failing at HEAD for that reason alone, unnoticed, before the daemon work
went anywhere near it.
"""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

from aegis.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _never_spawn_a_daemon(monkeypatch, tmp_path):
    def _boom(*_a, **_kw):
        raise AssertionError("the client spawned a daemon instead of refusing")

    monkeypatch.setattr("aegis.daemon.lifecycle._spawn_detached", _boom)
    monkeypatch.setenv("AEGIS_DAEMON_DIR", str(tmp_path / "daemons"))


def test_a_config_error_survives_an_eighty_column_console(
        tmp_path, monkeypatch):
    """The message must reach the user in one piece.

    `Console.print` wraps at the console width, so a long enough path
    pushed the text across a line break: "…: top \\nlevel must be a
    mapping". Nothing told us, because whether it broke depended on how
    long the temp directory happened to be. The directory here is long on
    purpose so the assertion does not depend on that luck.
    """
    deep = tmp_path / ("d" * 60)
    deep.mkdir()
    monkeypatch.chdir(deep)
    (deep / ".aegis.yaml").write_text("- not a mapping\n")

    r = runner.invoke(app, [], env={"COLUMNS": "80"})

    assert r.exit_code == 1, r.output
    assert "top level must be a mapping" in r.output, \
        f"message was re-wrapped by the console: {r.output!r}"


def test_serve_reports_a_config_error_the_same_way(tmp_path, monkeypatch):
    """`aegis serve` had the wrapped copy of this for as long as there were
    two boot paths.

    `run()` was fixed and `_run_serve` was not, because they were two
    sequences and the fix was scoped to one. Both resolve their boot through
    `resolve_boot` now and report through `_print_error`, so the wording and
    the wrapping cannot differ again by being edited in one place.
    """
    deep = tmp_path / ("s" * 60)
    deep.mkdir()
    monkeypatch.chdir(deep)
    (deep / ".aegis.yaml").write_text("- not a mapping\n")

    r = runner.invoke(app, ["serve"], env={"COLUMNS": "80"})

    assert r.exit_code == 1, r.output
    assert "top level must be a mapping" in r.output, \
        f"aegis serve re-wrapped the message: {r.output!r}"
