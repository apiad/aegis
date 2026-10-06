"""The ledger has a reader, so the artifact can be exercised as it is used."""
from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from aegis.cli_comms import comms_app, filter_rows
from aegis.comms.descriptors import CONVERSATION, Target
from aegis.comms.models import Envelope
from aegis.comms.persistence import CommsLedger

runner = CliRunner()


def _write(tmp_path: Path) -> CommsLedger:
    ledger = CommsLedger(tmp_path / ".aegis" / "state")
    ledger.write(Envelope(
        call_id="01A", ts="2026-08-11T10:00:00Z", from_handle="alice",
        to=Target("agent", "bob"), family=CONVERSATION, verb="handoff",
        thread="01A", outcome="ok", duration_ms=12))
    ledger.write(Envelope(
        call_id="01B", ts="2026-08-11T10:05:00Z", from_handle="bob",
        to=Target("queue", "general"), family=CONVERSATION, verb="enqueue",
        thread="01TASK", outcome="ok", duration_ms=30))
    ledger.write(Envelope(
        call_id="01C", ts="2026-08-11T10:06:00Z", from_handle="alice",
        to=None, family="introspection", verb="list_sessions",
        thread="01C", outcome="ok", duration_ms=3))
    return ledger


def test_filter_by_handle_matches_either_end(tmp_path: Path):
    rows = _write(tmp_path).read_all()
    assert [r["verb"] for r in filter_rows(rows, handle="bob")] == [
        "handoff", "enqueue"]


def test_filter_by_thread_and_family(tmp_path: Path):
    rows = _write(tmp_path).read_all()
    assert [r["verb"] for r in filter_rows(rows, thread="01TASK")] == [
        "enqueue"]
    assert [r["verb"] for r in
            filter_rows(rows, family="introspection")] == ["list_sessions"]


def test_filter_by_since_drops_older_rows(tmp_path: Path):
    rows = _write(tmp_path).read_all()
    kept = filter_rows(rows, since_iso="2026-08-11T10:05:00Z")
    assert [r["verb"] for r in kept] == ["enqueue", "list_sessions"]


def test_the_command_prints_one_line_per_call(tmp_path: Path, monkeypatch):
    """Driven through the top-level `aegis` app, not the standalone typer
    app. Invoked bare, a single-command typer app collapses and answers to
    no subcommand at all — so a test against `comms_app` directly passes on
    a form nobody can type. `aegis comms list` is what Alex runs."""
    from aegis.cli import app

    _write(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["comms", "list"])
    assert result.exit_code == 0
    assert "alice" in result.stdout and "bob" in result.stdout
    assert "handoff" in result.stdout
    assert len(result.stdout.strip().splitlines()) >= 3


def test_the_command_filters_through_the_real_entry_point(tmp_path,
                                                          monkeypatch):
    from aegis.cli import app

    _write(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["comms", "list", "--handle", "bob"])
    assert result.exit_code == 0
    assert "list_sessions" not in result.stdout
    assert "handoff" in result.stdout and "enqueue" in result.stdout


def test_the_command_says_so_when_the_ledger_is_empty(tmp_path, monkeypatch):
    from aegis.cli import app

    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["comms", "list"])
    assert result.exit_code == 0
    assert "no aegis calls recorded" in result.stdout.lower()
