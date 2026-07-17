import json
from decimal import Decimal
from pathlib import Path

from aegis.usage.aggregate import build_report, resolve_prices


def test_resolve_exact_alias():
    p = resolve_prices("claude-code", "claude-opus-4-7")
    assert p is not None and p.input > 0


def test_resolve_substring_fallback_for_newer_model():
    # claude-opus-4-8 is not in the shipped registry aliases → fall back
    # to the 'opus' family by substring.
    p = resolve_prices("claude-code", "claude-opus-4-8")
    assert p is not None and p.input > 0


def test_resolve_unknown_returns_none():
    assert resolve_prices("claude-code", "OpenCode") is None
    assert resolve_prices("claude-code", None) is None


# ---------------------------------------------------------------------------
# build_report fixtures (Task 3)
# ---------------------------------------------------------------------------

def _ev(**event):
    return json.dumps({"v": 1, "aegis_ts": event.pop("ts"), "event": event})


def _write_sessions(tmp_path: Path) -> Path:
    sess = tmp_path / ".aegis" / "state" / "sessions"
    sess.mkdir(parents=True)
    # Session A: opus, 2 turns, a Bash tool, cost 0.4 then 0.9 (no reset)
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
        _ev(ts="2026-06-01T12:05:00.000000Z", t="Result", duration_ms=2000,
            is_error=True, cost_usd=0.9,
            usage={"input": 5, "cache_creation": 0,
                   "cache_read": 500, "output": 80}),
    ]) + "\n")
    # Session B: model newer than registry, ONE reset (0.5 then 0.2)
    (sess / "beta.jsonl").write_text("\n".join([
        _ev(ts="2026-06-02T09:00:00.000000Z", t="SystemInit",
            session_id="b", model="claude-opus-4-8"),
        _ev(ts="2026-06-02T09:00:01.000000Z", t="Result", duration_ms=500,
            is_error=False, cost_usd=0.5,
            usage={"input": 1, "cache_creation": 10,
                   "cache_read": 20, "output": 10}),
        _ev(ts="2026-06-02T09:10:00.000000Z", t="Result", duration_ms=700,
            is_error=False, cost_usd=0.2,
            usage={"input": 1, "cache_creation": 0,
                   "cache_read": 30, "output": 5}),
    ]) + "\n")
    # Session C: no SystemInit.model, no cost_usd → falls back to est
    (sess / "gamma.jsonl").write_text("\n".join([
        _ev(ts="2026-06-03T08:00:00.000000Z", t="Result", duration_ms=300,
            is_error=False,
            usage={"input": 2, "cache_creation": 5,
                   "cache_read": 10, "output": 20}),
    ]) + "\n")
    # Session D: empty shell (only a hook line) → filtered out
    (sess / "delta.jsonl").write_text(
        _ev(ts="2026-06-03T08:00:00.000000Z", t="Unknown", raw="{}") + "\n")
    return tmp_path / ".aegis" / "state"


def test_build_report_basic(tmp_path):
    state = _write_sessions(tmp_path)
    r = build_report(state, default_model="claude-opus-4-7",
                     default_provider="claude-code")
    by = {s.handle: s for s in r.sessions}
    assert set(by) == {"alpha", "beta", "gamma"}          # delta filtered
    assert by["alpha"].turns == 2
    assert by["alpha"].errors == 1
    assert by["alpha"].tools["Bash"] == 1
    assert by["alpha"].billed_usd == Decimal("0.9")        # monotonic → last
    assert by["alpha"].model == "claude-opus-4-7"
    # beta: reset 0.5 then 0.2 → 0.7
    assert by["beta"].billed_usd == Decimal("0.7")
    assert by["beta"].gen_usd > 0                          # opus-4-8 resolved
    # gamma: no cost_usd → est flag, billed from token estimate
    assert by["gamma"].est is True
    assert by["gamma"].billed_usd > 0


def test_report_rollups(tmp_path):
    state = _write_sessions(tmp_path)
    r = build_report(state, default_model="claude-opus-4-7",
                     default_provider="claude-code")
    models = dict(r.by_model())
    assert "claude-opus-4-7" in models and "claude-opus-4-8" in models
    d = r.distribution()
    assert d["p50"] >= 0 and d["max"] >= d["p50"]
    tc = {name for name, _avg, _cnt in r.tool_correlation()}
    assert "Bash" in tc
    assert sum(v for _, v in r.by_dow()) == len(r.turns)


def test_since_filter(tmp_path):
    state = _write_sessions(tmp_path)
    r = build_report(state, default_model="claude-opus-4-7",
                     default_provider="claude-code", since="2026-06-02")
    handles = {s.handle for s in r.sessions}
    assert "alpha" not in handles and "beta" in handles
