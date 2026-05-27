"""Tests for `aegis budget list` / `show` CLI verbs.

Mirrors the `tests/test_cli_schedule_remote.py` pattern: seeds a
minimal `.aegis.yaml` in tmp_path, monkeypatches remote client
functions, and drives the app via CliRunner.
"""
from __future__ import annotations

import os
from pathlib import Path

from typer.testing import CliRunner

from aegis.cli_budget import app as budget_app

runner = CliRunner()


def _seed(root: Path) -> None:
    """Write a minimal .aegis.yaml into root."""
    (root / ".aegis.yaml").write_text(
        "default_agent: c\n"
        "agents:\n"
        "  c:\n"
        "    provider: claude-code\n"
        "    model: haiku\n"
        "queues:\n"
        "  impl:\n"
        "    agent: c\n"
        "    max_parallel: 1\n"
        "  budgeted:\n"
        "    agent: c\n"
        "    max_parallel: 1\n"
        "    budgets:\n"
        "      - usd: '1.00'\n"
        "        window: 1d\n"
        "remotes:\n"
        "  vps:\n"
        "    url: https://example.invalid\n"
        "    token: t\n"
        "    peer_name: zion\n"
    )


def _chdir(path: Path):
    prev = Path.cwd()
    os.chdir(path)
    return prev


def test_budget_list_runs(tmp_path: Path, monkeypatch) -> None:
    """`aegis budget list` invokes without error against a minimal config."""
    _seed(tmp_path)
    prev = _chdir(tmp_path)
    try:
        result = runner.invoke(budget_app, ["list"])
    finally:
        os.chdir(prev)
    assert result.exit_code == 0, result.output


def test_budget_show_unknown_queue_errors(tmp_path: Path, monkeypatch) -> None:
    """`aegis budget show ghost` exits non-zero with an error message."""
    _seed(tmp_path)
    prev = _chdir(tmp_path)
    try:
        result = runner.invoke(budget_app, ["show", "ghost"])
    finally:
        os.chdir(prev)
    assert result.exit_code != 0
    assert "unknown queue" in result.output


def test_budget_list_remote_calls_client(tmp_path: Path, monkeypatch) -> None:
    """`aegis budget list --remote vps` invokes remote_budget_list."""
    _seed(tmp_path)
    called_with = {}

    async def _fake_list(spec):
        called_with["spec"] = spec
        return {"queues": [
            {"name": "impl", "budgets_count": 0, "status": "no-budget"},
        ]}

    monkeypatch.setattr("aegis.cli_budget.remote_budget_list", _fake_list)
    prev = _chdir(tmp_path)
    try:
        result = runner.invoke(budget_app, ["list", "--remote", "vps"])
    finally:
        os.chdir(prev)
    assert result.exit_code == 0, result.output
    assert "spec" in called_with, "remote_budget_list was not called"


def test_budget_show_with_budget(tmp_path: Path, monkeypatch) -> None:
    """`aegis budget show budgeted` exits zero and renders a table."""
    _seed(tmp_path)
    prev = _chdir(tmp_path)
    try:
        result = runner.invoke(budget_app, ["show", "budgeted"])
    finally:
        os.chdir(prev)
    assert result.exit_code == 0, result.output
    # Should show the constraint column header
    assert "CONSTRAINT" in result.output or "usd" in result.output
