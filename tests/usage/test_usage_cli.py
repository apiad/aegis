"""``aegis usage``, invoked as a person would, on a store aegis wrote."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from typer.testing import CliRunner

from aegis.cli import app
from aegis.journal.service import Journal

from .stores import assistant, store

runner = CliRunner()


def _root(tmp_path):
    (tmp_path / ".aegis.yaml").write_text("agents: {}\n")
    tool = assistant("msg_1")
    tool["message"]["content"] = [{"type": "tool_use", "name": "Bash"}]
    store(
        tmp_path / ".aegis" / "state",
        "20260601-120000-aaaaaa",
        tmp_path,
        [
            (
                "2026-06-01T12:00:00Z",
                {"type": "system", "subtype": "init", "model": "claude-opus-4-7"},
            ),
            ("2026-06-01T12:00:01Z", tool),
            (
                "2026-06-01T12:00:02Z",
                {"type": "result", "total_cost_usd": 0.4, "usage": {}},
            ),
        ],
        handle="alpha",
    )
    # As aegis serve would have: the journal and its usage tables, derived.
    Journal(tmp_path / ".aegis" / "state", None).rebuild()
    return tmp_path


def invoke(*args: str):
    return runner.invoke(app, list(args), env={"COLUMNS": "200"})


def test_the_dashboard_names_the_session(tmp_path):
    root = _root(tmp_path)
    res = invoke("usage", "--root", str(root))
    assert res.exit_code == 0, res.output
    assert "AEGIS USAGE" in res.output
    assert "alpha" in res.output


def test_every_view_runs(tmp_path):
    root = _root(tmp_path)
    for args in (
        ["--by", "dow"],
        ["--by", "month"],
        ["--by", "hour"],
        ["--sessions"],
        ["--tools"],
        ["--session", "alpha"],
        ["--model", "claude-opus-4-7"],
        ["--tz", "Europe/Madrid"],
        ["--since", "2026-06-01"],
    ):
        res = invoke("usage", "--root", str(root), *args)
        assert res.exit_code == 0, (args, res.output)


def test_a_bad_by_is_refused_by_name(tmp_path):
    res = invoke("usage", "--root", str(_root(tmp_path)), "--by", "fortnight")
    assert res.exit_code == 2
    assert "'fortnight'" in res.output


def test_an_empty_store_says_so(tmp_path):
    (tmp_path / ".aegis.yaml").write_text("agents: {}\n")
    res = invoke("usage", "--root", str(tmp_path))
    assert res.exit_code == 0
    assert "no sessions found" in res.output.lower()


def _recent(tmp_path):
    """Two sessions this week, in two repos of two owners, on two harnesses'
    worth of models."""
    (tmp_path / ".aegis.yaml").write_text("agents: {}\n")
    state = tmp_path / ".aegis" / "state"
    now = datetime.now(timezone.utc) - timedelta(days=1)
    iso = lambda dt: dt.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    for k, (log_id, model, cost) in enumerate(
        [("20261020-120000-aaaaaa", "claude-opus-4-7", 0.4),
         ("20261020-120000-bbbbbb", "claude-sonnet-4-6", 0.1)]
    ):  # fmt: skip
        tool = assistant(f"msg_{k}", model=model)
        tool["message"]["content"] = [
            {"type": "tool_use", "id": f"t{k}", "name": "Read",
             "input": {"file_path": str(tmp_path / f"repo{k}" / "a.py")}}
        ]  # fmt: skip
        store(state, log_id, tmp_path, [
            (iso(now), tool),
            (iso(now + timedelta(seconds=2)),
             {"type": "result", "total_cost_usd": cost, "usage": {}}),
        ], handle=f"s{k}")  # fmt: skip
    Journal(state, None).rebuild()
    return tmp_path


def test_split_and_measure_print_rows_and_totals_as_json(tmp_path):
    root = _recent(tmp_path)
    res = invoke("usage", "--root", str(root), "--since", "4w", "--split", "model",
                 "--measure", "cost", "--json")  # fmt: skip
    assert res.exit_code == 0, res.output
    out = json.loads(res.output)
    assert {r["model"]: r["cost"] for r in out["rows"]} == {
        "claude-opus-4-7": 0.4,
        "claude-sonnet-4-6": 0.1,
    }
    assert out["totals"]["cost"] == 0.5 and out["totals"]["sessions"] == 2


def test_where_filters(tmp_path):
    root = _recent(tmp_path)
    res = invoke(
        "usage", "--root", str(root), "--json", "--where", "model=claude-opus-4-7"
    )
    assert json.loads(res.output)["totals"]["cost"] == 0.4
    res = invoke("usage", "--root", str(root), "--json", "--where", "harness=codex")
    assert json.loads(res.output)["totals"]["cost"] == 0
    res = invoke("usage", "--root", str(root), "--where", "nothing=1")
    assert res.exit_code == 2 and "nothing=1" in res.output


def test_a_partial_backfill_says_how_far_it_came(tmp_path):
    root = _recent(tmp_path)
    con = sqlite3.connect(root / ".aegis" / "state" / "journal.db")
    con.execute("UPDATE usage_meta SET value = '7' WHERE key = 'backfill'")
    con.execute("DELETE FROM usage_turns WHERE log_id LIKE '%bbbbbb'")
    con.execute("DELETE FROM usage_parts WHERE log_id LIKE '%bbbbbb'")
    con.commit()
    con.close()
    res = invoke("usage", "--root", str(root))
    assert res.exit_code == 0, res.output
    assert "1 of about 2 sessions" in res.output
    assert "AEGIS USAGE" in res.output and "s0" in res.output
