import json
from pathlib import Path

import aegis.commands.builtins  # noqa: F401 — ensure /usage is registered
from aegis.commands import REGISTRY, CommandContext, dispatch
from aegis.config.roots import AegisRoots


def _ev(**event):
    return json.dumps({"v": 1, "aegis_ts": event.pop("ts"), "event": event})


def _mk(tmp_path: Path) -> Path:
    """Build a project *below* tmp_path and return its root.

    Deliberately not tmp_path itself: the autouse ``isolated_project_dir``
    fixture chdirs to tmp_path, so a project rooted there is also the
    process cwd and a test cannot tell the two apart. One level down, a
    ``/usage`` that resolved from the cwd finds nothing.
    """
    root = tmp_path / "project"
    sess = root / ".aegis" / "state" / "sessions"
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
    (root / ".aegis.yaml").write_text(
        "agents:\n  opus:\n    provider: claude-code\n    model: opus\n"
        "default_agent: opus\n")
    return root


class _Bridge:
    """A bridge is all /usage needs from the app: the roots it aggregates
    under."""

    def __init__(self, root: Path) -> None:
        self.roots = AegisRoots.for_project(root)


def _ctx(root: Path):
    return CommandContext(bridge=_Bridge(root), handle="me")


def test_usage_registered():
    assert "usage" in REGISTRY
    assert REGISTRY["usage"].source == "builtin"


async def test_usage_dashboard(tmp_path):
    root = _mk(tmp_path)
    res = await dispatch("/usage", _ctx(root))
    assert res.ok, res.body
    assert "AEGIS USAGE" in res.body
    assert "billed" in res.title
    # token totals appear alongside cost (alpha: 5+100+200+50 = 355 tokens)
    assert "TOKENS" in res.body
    assert "355 tokens" in res.body
    assert "total" in res.body


async def test_usage_views(tmp_path):
    root = _mk(tmp_path)
    for v in ("tools", "sessions", "month", "dow", "hour"):
        res = await dispatch(f"/usage {v}", _ctx(root))
        assert res.ok, (v, res.title, res.body)
        assert res.body


async def test_usage_unknown_view(tmp_path):
    root = _mk(tmp_path)
    res = await dispatch("/usage bogus", _ctx(root))
    assert not res.ok
    assert "bogus" in res.title


async def test_usage_no_sessions(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / ".aegis.yaml").write_text(
        "agents:\n  opus:\n    provider: claude-code\n    model: opus\n"
        "default_agent: opus\n")
    res = await dispatch("/usage", _ctx(root))
    assert res.ok
    assert "no session logs" in res.title.lower()
