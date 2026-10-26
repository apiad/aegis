"""The usage tables over the stores report.py read, with the numbers it gave.

report.py summed Claude's running totals per session; the tables count them
per turn. Its billed totals here were alpha 0.9, beta 0.7 (a resume) and an
estimate for gamma from its tokens: the tables give the same.
"""

import sqlite3
from pathlib import Path

import pytest

from aegis.journal.service import Journal
from aegis.usage.prices import prices_for
from aegis.usage.query import UsageQuery, run

from .stores import epoch, store


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


def _derived(state: Path) -> sqlite3.Connection:
    j = Journal(state, None)
    j.rebuild()
    return sqlite3.connect(j.path)


def _by_session(con, **kw):
    a = run(con, UsageQuery(0, 2e9, split=("session",), **kw))
    return {r["session"][-6:]: r for r in a.rows}


def test_sessions_turns_and_billed_cost(state):
    con = _derived(state)
    by = _by_session(con)
    assert set(by) == {"aaaaaa", "bbbbbb", "cccccc"}
    assert by["aaaaaa"]["turns"] == 2 and by["aaaaaa"]["calls"] == 1
    assert by["aaaaaa"]["cost"] == pytest.approx(0.9)
    assert by["bbbbbb"]["cost"] == pytest.approx(0.7)
    models = {t[0] for t in con.execute("SELECT DISTINCT model FROM usage_turns")}
    assert {"claude-opus-4-7", "claude-opus-4-8"} <= models


def test_a_session_without_a_reported_cost_is_priced_from_its_tokens(state):
    con = _derived(state)
    haiku = prices_for("claude-haiku-4-5")
    expected = haiku.cost(inp=2, out=20, cc5=5, cc1=0, cache_read=10)
    assert con.execute(
        "SELECT cost_kind, model, cost FROM usage_turns WHERE log_id LIKE '%cccccc'"
    ).fetchone() == ("priced", "claude-haiku-4-5", pytest.approx(float(expected)))
    a = run(con, UsageQuery(0, 2e9))
    assert a.estimated == pytest.approx(float(expected) / a.totals["cost"], abs=1e-6)


def test_billed_parity_with_the_old_report(state):
    """report.py's total over these stores was 1.6 billed plus gamma's
    estimate; the tables' billed rows give 1.6 within 1%."""
    con = _derived(state)
    billed = con.execute(
        "SELECT SUM(cost) FROM usage_turns WHERE cost_kind = 'billed'"
    ).fetchone()[0]
    assert billed == pytest.approx(1.6, rel=0.01)


def test_total_tokens_come_from_results(state):
    con = _derived(state)
    assert con.execute(
        "SELECT SUM(tok_in), SUM(tok_out), SUM(tok_cw), SUM(tok_cr) FROM usage_turns"
    ).fetchone() == (
        5 + 5 + 1 + 1 + 2,
        50 + 80 + 10 + 5 + 20,
        100 + 10 + 5,
        200 + 500 + 20 + 30 + 10,
    )


def test_since_filters_turns(state):
    con = _derived(state)
    since = epoch("2026-06-02T00:00:00Z")
    a = run(con, UsageQuery(since, 2e9, split=("session",)))
    assert {r["session"][-6:] for r in a.rows} == {"bbbbbb", "cccccc"}
