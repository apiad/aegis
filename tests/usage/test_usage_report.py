"""The dashboard, built from aegis's store."""

from decimal import Decimal
from pathlib import Path

import pytest

from aegis.usage.prices import prices_for
from aegis.usage.report import build_report

from .stores import store


def _result(cost: float | None, *, is_error=False, ms=1000, **usage) -> dict:
    line = {
        "type": "result",
        "is_error": is_error,
        "duration_ms": ms,
        "usage": {
            "input_tokens": usage.get("inp", 0),
            "output_tokens": usage.get("out", 0),
            "cache_read_input_tokens": usage.get("read", 0),
            "cache_creation_input_tokens": usage.get("write", 0),
        },
    }
    if cost is not None:
        line["total_cost_usd"] = cost
    return line


def _init(model: str) -> dict:
    return {"type": "system", "subtype": "init", "model": model}


def _tool(name: str) -> dict:
    return {
        "type": "assistant",
        "message": {"id": "msg_t", "content": [{"type": "tool_use", "name": name}]},
    }


@pytest.fixture
def state(tmp_path) -> Path:
    s = tmp_path / ".aegis" / "state"
    cwd = tmp_path
    # alpha: two turns, one failed, a Bash call, running cost 0.4 then 0.9.
    store(
        s,
        "20260601-120000-aaaaaa",
        cwd,
        [
            ("2026-06-01T12:00:00Z", _init("claude-opus-4-7")),
            ("2026-06-01T12:00:01Z", _tool("Bash")),
            ("2026-06-01T12:00:02Z", _result(0.4, inp=5, write=100, read=200, out=50)),
            (
                "2026-06-01T12:05:00Z",
                _result(0.9, is_error=True, ms=2000, inp=5, read=500, out=80),
            ),
        ],
        handle="alpha",
    )
    # beta: a resume restarts the running total, 0.5 then 0.2.
    store(
        s,
        "20260602-090000-bbbbbb",
        cwd,
        [
            ("2026-06-02T09:00:00Z", _init("claude-opus-4-8")),
            ("2026-06-02T09:00:01Z", _result(0.5, inp=1, write=10, read=20, out=10)),
            ("2026-06-02T09:10:00Z", _result(0.2, inp=1, read=30, out=5)),
        ],
        handle="beta",
    )
    # gamma: no cost reported and no model in Claude's lines; the spawn's
    # model prices it, marked estimated.
    store(
        s,
        "20260603-080000-cccccc",
        cwd,
        [("2026-06-03T08:00:00Z", _result(None, inp=2, write=5, read=10, out=20))],
        handle="gamma",
        model="claude-haiku-4-5",
    )
    # delta: a session that never ran a turn.
    store(
        s,
        "20260603-090000-dddddd",
        cwd,
        [("2026-06-03T09:00:00Z", {"type": "system", "subtype": "hook_started"})],
        handle="delta",
    )
    return s


def test_sessions_turns_and_billed_cost(state):
    r = build_report(state)
    by = {s.handle: s for s in r.sessions}
    assert set(by) == {"alpha", "beta", "gamma"}
    assert by["alpha"].turns == 2
    assert by["alpha"].errors == 1
    assert by["alpha"].tools["Bash"] == 1
    assert by["alpha"].billed_usd == Decimal("0.9")
    assert by["alpha"].model == "claude-opus-4-7"
    assert by["beta"].billed_usd == Decimal("0.7")
    assert by["beta"].gen_usd > 0


def test_a_session_without_a_reported_cost_is_estimated_from_its_tokens(state):
    gamma = next(s for s in build_report(state).sessions if s.handle == "gamma")
    haiku = prices_for("claude-haiku-4-5")
    assert haiku is not None
    expected = haiku.cost(inp=2, out=20, cc5=5, cc1=0, cache_read=10)
    assert gamma.est is True
    assert gamma.model == "claude-haiku-4-5"
    assert gamma.billed_usd == expected


def test_rollups(state):
    r = build_report(state)
    models = dict(r.by_model())
    assert "claude-opus-4-7" in models and "claude-opus-4-8" in models
    d = r.distribution()
    assert d["max"] >= d["p50"] >= 0
    assert "Bash" in {name for name, _avg, _cnt in r.tool_correlation()}
    assert sum(v for _, v in r.by_dow()) == len(r.turns)


def test_total_tokens_come_from_results(state):
    tok = build_report(state).total_tokens()
    assert tok == {
        "input": 5 + 5 + 1 + 1 + 2,
        "output": 50 + 80 + 10 + 5 + 20,
        "cache_creation": 100 + 10 + 5,
        "cache_read": 200 + 500 + 20 + 30 + 10,
    }


def test_since_and_session_filters(state):
    assert {s.handle for s in build_report(state, since="2026-06-02").sessions} == {
        "beta",
        "gamma",
    }
    assert [s.handle for s in build_report(state, handle="beta").sessions] == ["beta"]
