import json
from pathlib import Path

import aegis.commands.builtins  # noqa: F401 — ensure /usage is registered
from aegis.commands import REGISTRY, CommandContext, dispatch


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


def _ctx():
    return CommandContext(bridge=object(), handle="me")


def test_usage_registered():
    assert "usage" in REGISTRY
    assert REGISTRY["usage"].source == "builtin"


async def test_usage_dashboard(tmp_path, monkeypatch):
    _mk(tmp_path)
    monkeypatch.chdir(tmp_path)
    res = await dispatch("/usage", _ctx())
    assert res.ok, res.body
    assert "AEGIS USAGE" in res.body
    assert "billed" in res.title


async def test_usage_views(tmp_path, monkeypatch):
    _mk(tmp_path)
    monkeypatch.chdir(tmp_path)
    for v in ("tools", "sessions", "month", "dow", "hour"):
        res = await dispatch(f"/usage {v}", _ctx())
        assert res.ok, (v, res.title, res.body)
        assert res.body


async def test_usage_unknown_view(tmp_path, monkeypatch):
    _mk(tmp_path)
    monkeypatch.chdir(tmp_path)
    res = await dispatch("/usage bogus", _ctx())
    assert not res.ok
    assert "bogus" in res.title


async def test_usage_no_sessions(tmp_path, monkeypatch):
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n  opus:\n    provider: claude-code\n    model: opus\n"
        "default_agent: opus\n")
    monkeypatch.chdir(tmp_path)
    res = await dispatch("/usage", _ctx())
    assert res.ok
    assert "no session logs" in res.title.lower()
