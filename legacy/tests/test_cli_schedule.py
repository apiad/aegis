"""Tests for the ``aegis schedule ...`` Typer subcommand.

These exercise the CLI in-process via Typer's runner; no real
scheduler is started. The CLI commands read snapshot + JSONL from
disk, so tests construct minimal fixtures rather than running a
serve."""
from __future__ import annotations

import json
import os
from pathlib import Path

from typer.testing import CliRunner

from aegis.cli_schedule import app as schedule_app

runner = CliRunner()


def _write_yaml(path: Path, body: str) -> None:
    path.write_text(body)


def _seed(root: Path, *, enabled: bool = True) -> None:
    (root / ".aegis.yaml").write_text(
        "default_agent: c\n"
        "agents:\n"
        "  c:\n"
        "    provider: claude-code\n"
        "    model: haiku\n"
        "schedules:\n"
        "  eod:\n"
        "    workflow: prompt\n"
        "    args: {agent: c, text: hi}\n"
        "    cron: '0 2 * * *'\n"
        "    timezone: UTC\n"
        f"    enabled: {'true' if enabled else 'false'}\n"
    )


def _chdir(path: Path):
    """Context manager replacement that returns the previous cwd."""
    prev = Path.cwd()
    os.chdir(path)
    return prev


def test_list_shows_armed_schedule(tmp_path: Path) -> None:
    _seed(tmp_path)
    prev = _chdir(tmp_path)
    try:
        result = runner.invoke(schedule_app, ["list"])
    finally:
        os.chdir(prev)
    assert result.exit_code == 0
    assert "eod" in result.stdout
    assert "armed" in result.stdout


def test_list_shows_paused_when_disabled(tmp_path: Path) -> None:
    _seed(tmp_path, enabled=False)
    prev = _chdir(tmp_path)
    try:
        result = runner.invoke(schedule_app, ["list"])
    finally:
        os.chdir(prev)
    assert "paused" in result.stdout


def test_show_unknown_returns_nonzero(tmp_path: Path) -> None:
    _seed(tmp_path)
    prev = _chdir(tmp_path)
    try:
        result = runner.invoke(schedule_app, ["show", "ghost"])
    finally:
        os.chdir(prev)
    assert result.exit_code != 0


def test_disable_then_list_paused(tmp_path: Path) -> None:
    _seed(tmp_path)
    prev = _chdir(tmp_path)
    try:
        r1 = runner.invoke(schedule_app, ["disable", "eod"])
        r2 = runner.invoke(schedule_app, ["list"])
    finally:
        os.chdir(prev)
    assert r1.exit_code == 0
    assert "paused" in r2.stdout


def test_enable_round_trip(tmp_path: Path) -> None:
    _seed(tmp_path, enabled=False)
    prev = _chdir(tmp_path)
    try:
        r1 = runner.invoke(schedule_app, ["enable", "eod"])
        r2 = runner.invoke(schedule_app, ["list"])
    finally:
        os.chdir(prev)
    assert r1.exit_code == 0
    assert "armed" in r2.stdout


def test_logs_missing_returns_nonzero(tmp_path: Path) -> None:
    _seed(tmp_path)
    prev = _chdir(tmp_path)
    try:
        result = runner.invoke(schedule_app, ["logs", "eod"])
    finally:
        os.chdir(prev)
    assert result.exit_code != 0


def test_logs_tails_jsonl(tmp_path: Path) -> None:
    _seed(tmp_path)
    log_dir = tmp_path / ".aegis" / "state" / "schedules"
    log_dir.mkdir(parents=True)
    log = log_dir / "eod.jsonl"
    log.write_text(
        json.dumps({"ts": "x", "schedule": "eod",
                    "event": "fire_requested", "task_id": "a"}) + "\n"
        + json.dumps({"ts": "y", "schedule": "eod",
                      "event": "fire_completed", "task_id": "a",
                      "status": "ok"}) + "\n"
    )
    prev = _chdir(tmp_path)
    try:
        result = runner.invoke(schedule_app, ["logs", "eod"])
    finally:
        os.chdir(prev)
    assert result.exit_code == 0
    assert "fire_completed" in result.stdout
