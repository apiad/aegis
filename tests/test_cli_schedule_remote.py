"""Tests for the remote-routed `aegis schedule ...` verbs.

Patches the `remote_schedule_*` client functions at the
`aegis.cli_schedule` import site to avoid real HTTP.
"""
from __future__ import annotations

import os
from pathlib import Path

from typer.testing import CliRunner

from aegis.cli_schedule import app as schedule_app

runner = CliRunner()


def _seed(root: Path) -> None:
    (root / ".aegis.yaml").write_text(
        "default_agent: c\n"
        "agents:\n"
        "  c:\n"
        "    provider: claude-code\n"
        "    model: haiku\n"
        "schedules:\n"
        "  nightly:\n"
        "    workflow: prompt\n"
        "    args: {agent: c, text: hi}\n"
        "    cron: '0 2 * * *'\n"
        "    timezone: UTC\n"
        "    enabled: true\n"
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


async def _ok_push(spec, *, name, spec_body, pushed_from):
    _ok_push.last = {"spec": spec, "name": name,
                     "spec_body": spec_body, "pushed_from": pushed_from}
    return {"name": name, "written_to": f".aegis/schedules/{name}.yaml"}


def test_schedule_push_to_remote_reads_local_config(
        tmp_path: Path, monkeypatch) -> None:
    _seed(tmp_path)
    monkeypatch.setattr(
        "aegis.cli_schedule.remote_schedule_push", _ok_push)
    prev = _chdir(tmp_path)
    try:
        result = runner.invoke(
            schedule_app, ["push", "--to", "vps", "--name", "nightly"])
    finally:
        os.chdir(prev)
    assert result.exit_code == 0, result.output
    assert "pushed nightly" in result.output
    captured = _ok_push.last
    assert captured["name"] == "nightly"
    assert captured["spec_body"].get("workflow") == "prompt"
    assert captured["spec_body"].get("cron") == "0 2 * * *"
    # peer_name on remote_plane is missing → "unknown"
    assert captured["pushed_from"] == "peer:unknown"


def test_schedule_push_from_file(tmp_path: Path, monkeypatch) -> None:
    _seed(tmp_path)
    spec_file = tmp_path / "myjob.yaml"
    spec_file.write_text(
        "workflow: prompt\n"
        "args: {agent: c, text: hello}\n"
        "fire_at: '2026-06-01T10:00:00Z'\n"
    )
    monkeypatch.setattr(
        "aegis.cli_schedule.remote_schedule_push", _ok_push)
    prev = _chdir(tmp_path)
    try:
        result = runner.invoke(
            schedule_app,
            ["push", "--to", "vps", "--file", str(spec_file)])
    finally:
        os.chdir(prev)
    assert result.exit_code == 0, result.output
    captured = _ok_push.last
    assert captured["name"] == "myjob"
    assert captured["spec_body"]["fire_at"] == "2026-06-01T10:00:00Z"


def test_schedule_push_unknown_remote_errors(
        tmp_path: Path, monkeypatch) -> None:
    _seed(tmp_path)
    prev = _chdir(tmp_path)
    try:
        result = runner.invoke(
            schedule_app, ["push", "--to", "ghost", "--name", "nightly"])
    finally:
        os.chdir(prev)
    assert result.exit_code == 1
    assert "unknown remote" in result.output


def test_schedule_list_remote(tmp_path: Path, monkeypatch) -> None:
    _seed(tmp_path)

    async def _fake(spec):
        return {"schedules": [
            {"name": "remote-a", "cron": "*/5 * * * *",
             "next_fire": "2026-05-25T20:00:00", "fire_count": 3,
             "enabled": True, "source": "pushed"},
            {"name": "remote-b", "fire_at": "2026-06-01T10:00:00",
             "next_fire": None, "fire_count": 0,
             "enabled": False, "source": "pushed"},
        ]}

    monkeypatch.setattr(
        "aegis.cli_schedule.remote_schedule_list", _fake)
    prev = _chdir(tmp_path)
    try:
        result = runner.invoke(schedule_app, ["list", "--remote", "vps"])
    finally:
        os.chdir(prev)
    assert result.exit_code == 0, result.output
    assert "remote-a" in result.output
    assert "remote-b" in result.output
    assert "armed" in result.output
    assert "paused" in result.output


def test_schedule_show_remote(tmp_path: Path, monkeypatch) -> None:
    _seed(tmp_path)

    async def _fake(spec, name):
        return {
            "name": name, "source": "pushed",
            "spec": {"workflow": "prompt", "cron": "0 2 * * *"},
            "runtime": {"next_fire": "2026-05-26T02:00:00",
                        "fire_count": 1, "enabled": True},
            "pushed_from": "peer:zion", "pushed_at": "2026-05-25T10:00:00",
        }

    monkeypatch.setattr(
        "aegis.cli_schedule.remote_schedule_show", _fake)
    prev = _chdir(tmp_path)
    try:
        result = runner.invoke(
            schedule_app, ["show", "nightly", "--remote", "vps"])
    finally:
        os.chdir(prev)
    assert result.exit_code == 0, result.output
    assert "nightly" in result.output
    assert "peer:zion" in result.output


def test_schedule_logs_remote(tmp_path: Path, monkeypatch) -> None:
    _seed(tmp_path)

    async def _fake(spec, name, tail=50):
        return {"records": [
            {"ts": "2026-05-25T10:00:00", "schedule": name,
             "event": "fire_completed", "status": "ok"},
        ]}

    monkeypatch.setattr(
        "aegis.cli_schedule.remote_schedule_logs", _fake)
    prev = _chdir(tmp_path)
    try:
        result = runner.invoke(
            schedule_app, ["logs", "nightly", "--remote", "vps"])
    finally:
        os.chdir(prev)
    assert result.exit_code == 0, result.output
    assert "fire_completed" in result.output


def test_schedule_remove_remote(tmp_path: Path, monkeypatch) -> None:
    _seed(tmp_path)

    async def _fake(spec, name):
        return {"ok": True}

    monkeypatch.setattr(
        "aegis.cli_schedule.remote_schedule_remove", _fake)
    prev = _chdir(tmp_path)
    try:
        result = runner.invoke(
            schedule_app, ["remove", "nightly", "--remote", "vps"])
    finally:
        os.chdir(prev)
    assert result.exit_code == 0, result.output
    assert "removed nightly" in result.output
