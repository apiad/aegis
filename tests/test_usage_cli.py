import json
from pathlib import Path

from typer.testing import CliRunner

from aegis.cli_usage import app

runner = CliRunner()


def _ev(**event):
    return json.dumps({"v": 1, "aegis_ts": event.pop("ts"), "event": event})


def _mk(tmp_path: Path):
    sess = tmp_path / ".aegis" / "state" / "sessions"
    sess.mkdir(parents=True)
    (sess / "alpha.jsonl").write_text("\n".join([
        _ev(ts="2026-06-01T12:00:00.000000Z", t="SystemInit",
            session_id="a", model="claude-opus-4-7"),
        _ev(ts="2026-06-01T12:00:01.000000Z", t="ToolUse", name="Bash",
            summary="ls", usage={"input": 5, "cache_creation": 100,
                                 "cache_read": 200, "output": 0}),
        _ev(ts="2026-06-01T12:00:02.000000Z", t="Result", duration_ms=1000,
            is_error=False, cost_usd=0.4,
            usage={"input": 5, "cache_creation": 100,
                   "cache_read": 200, "output": 50}),
    ]) + "\n")
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n  opus:\n    provider: claude-code\n    model: opus\n"
        "default_agent: opus\n")
    return tmp_path


def test_dashboard_runs(tmp_path, monkeypatch):
    root = _mk(tmp_path)
    monkeypatch.chdir(root)
    res = runner.invoke(app, [])
    assert res.exit_code == 0, res.output
    assert "AEGIS USAGE" in res.output
    assert "alpha" in res.output


def test_flags_run(tmp_path, monkeypatch):
    root = _mk(tmp_path)
    monkeypatch.chdir(root)
    for args in (["--by", "dow"], ["--sessions"], ["--tools"],
                 ["--session", "alpha"]):
        res = runner.invoke(app, args)
        assert res.exit_code == 0, (args, res.output)


def test_no_sessions_message(tmp_path, monkeypatch):
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n  opus:\n    provider: claude-code\n    model: opus\n"
        "default_agent: opus\n")
    monkeypatch.chdir(tmp_path)
    res = runner.invoke(app, [])
    assert res.exit_code == 0
    assert "no session logs" in res.output.lower()
