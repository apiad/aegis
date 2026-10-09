"""``aegis usage``, invoked as a person would, on a store aegis wrote."""

from typer.testing import CliRunner

from aegis.cli import app

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
    for args in (["--by", "dow"], ["--sessions"], ["--tools"], ["--session", "alpha"]):
        res = invoke("usage", "--root", str(root), *args)
        assert res.exit_code == 0, (args, res.output)


def test_a_bad_by_is_refused_by_name(tmp_path):
    res = invoke("usage", "--root", str(_root(tmp_path)), "--by", "week")
    assert res.exit_code == 2
    assert "'week'" in res.output


def test_an_empty_store_says_so(tmp_path):
    (tmp_path / ".aegis.yaml").write_text("agents: {}\n")
    res = invoke("usage", "--root", str(tmp_path))
    assert res.exit_code == 0
    assert "no sessions found" in res.output.lower()
