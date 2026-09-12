"""The command surface. Typer-level, with the transport stubbed — the
socket is tested in tests/daemon; what is unproven here is the wiring."""
import os
import time

import pytest
from typer.testing import CliRunner

from aegis import cli
from aegis.daemon import registry as dreg

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_DAEMON_DIR", str(tmp_path / "daemons"))


def _record(root, pid):
    dreg.record(dreg.DaemonRecord(
        root=root, pid=pid, socket=root / "d.sock", started=time.time(),
        version="test"))


def test_ls_lists_a_live_daemon(tmp_path):
    _record(tmp_path, os.getpid())
    result = runner.invoke(cli.app, ["ls"])
    assert result.exit_code == 0
    assert str(tmp_path) in result.output


def test_ls_with_no_daemons_says_so_and_exits_zero(tmp_path):
    result = runner.invoke(cli.app, ["ls"])
    assert result.exit_code == 0
    assert "no aegis daemons" in result.output.lower()


def test_kill_signals_the_daemon_for_this_root(tmp_path, monkeypatch):
    _record(tmp_path, os.getpid())
    signalled = []
    monkeypatch.setattr(dreg, "kill",
                        lambda rec, **kw: signalled.append(rec.pid) or True)
    result = runner.invoke(cli.app, ["kill", "--cwd", str(tmp_path)])
    assert result.exit_code == 0
    assert signalled == [os.getpid()]


def test_kill_with_no_daemon_exits_nonzero(tmp_path):
    result = runner.invoke(cli.app, ["kill", "--cwd", str(tmp_path)])
    assert result.exit_code != 0


def test_kill_all_stops_every_daemon(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    _record(a, os.getpid())
    _record(b, os.getpid())
    signalled = []
    import aegis.daemon.registry as _r
    real_kill = _r.kill
    _r.kill = lambda rec, **kw: signalled.append(rec.root) or True
    try:
        result = runner.invoke(cli.app, ["kill", "--all"])
    finally:
        _r.kill = real_kill
    assert result.exit_code == 0
    assert set(signalled) == {a.resolve(), b.resolve()}


def test_attach_ensures_a_daemon_then_pipes(tmp_path, monkeypatch):
    calls = {}

    async def _fake_ensure(root, **kw):
        calls["root"] = root
        return tmp_path / "d.sock"

    async def _fake_attach(path, view_id, **kw):
        calls["path"] = path
        calls["view_id"] = view_id

    monkeypatch.setattr(cli, "_ensure_daemon", _fake_ensure)
    monkeypatch.setattr(cli, "_attach", _fake_attach)
    result = runner.invoke(cli.app, ["attach", "--cwd", str(tmp_path),
                                     "--view", "review"])
    assert result.exit_code == 0, result.output
    assert calls["view_id"] == "review"
    assert calls["path"] == tmp_path / "d.sock"


def test_attach_reports_a_failed_autostart_instead_of_hanging(
        tmp_path, monkeypatch):
    from aegis.daemon.lifecycle import SpawnFailed

    async def _boom(root, **kw):
        raise SpawnFailed("did not come up")

    monkeypatch.setattr(cli, "_ensure_daemon", _boom)
    result = runner.invoke(cli.app, ["attach", "--cwd", str(tmp_path)])
    assert result.exit_code != 0
    assert "did not come up" in result.output


def test_bare_aegis_goes_through_the_daemon(tmp_path, monkeypatch):
    """`aegis` is a client now. This is the behaviour change."""
    (tmp_path / ".aegis.yaml").write_text(
        "default_agent: a\nagents:\n  a:\n    harness: claude-code\n"
        "    model: opus\n", encoding="utf-8")
    calls = {}

    async def _fake_ensure(root, **kw):
        calls["root"] = root
        return tmp_path / "d.sock"

    async def _fake_attach(path, view_id, **kw):
        calls["view_id"] = view_id

    monkeypatch.setattr(cli, "_ensure_daemon", _fake_ensure)
    monkeypatch.setattr(cli, "_attach", _fake_attach)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli.app, [])
    assert result.exit_code == 0, result.output
    assert calls["root"] == tmp_path.resolve()
    assert calls["view_id"], "attached without a view id"


def test_foreground_takes_the_old_path_not_the_daemon(tmp_path, monkeypatch):
    """--foreground is the escape hatch for CI, uvx and debugging the
    daemon itself. It must not touch ensure_daemon."""
    (tmp_path / ".aegis.yaml").write_text(
        "default_agent: a\nagents:\n  a:\n    harness: claude-code\n"
        "    model: opus\n", encoding="utf-8")
    touched = []

    async def _never(root, **kw):
        touched.append(root)
        return tmp_path / "d.sock"

    monkeypatch.setattr(cli, "_ensure_daemon", _never)
    ran = []

    def _capture(coro):
        ran.append(coro)
        coro.close()

    monkeypatch.setattr(cli.asyncio, "run", _capture)
    monkeypatch.chdir(tmp_path)
    runner.invoke(cli.app, ["--foreground"])
    assert touched == [], "--foreground went through the daemon"
    assert ran, "--foreground did not boot a brain in this process"
