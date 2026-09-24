import json

from typer.testing import CliRunner

from aegis.cli_usage import app

runner = CliRunner()


def test_usage_repo_prints_a_table_with_the_error_bar(cost_tree, monkeypatch):
    monkeypatch.chdir(cost_tree)
    result = runner.invoke(
        app,
        [
            "repo",
            str(cost_tree / "repos" / "aegis"),
            "--no-foreign",
            "--state",
            str(cost_tree / ".aegis" / "state"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "aegis" in result.output
    assert "proportional" in result.output.lower()
    assert "strict" in result.output.lower()
    assert "coverage" in result.output.lower()


def test_usage_repo_json_writes_the_cache_under_state(cost_tree, monkeypatch):
    monkeypatch.chdir(cost_tree)
    state = cost_tree / ".aegis" / "state"
    result = runner.invoke(
        app,
        [
            "repo",
            str(cost_tree / "repos" / "aegis"),
            "--no-foreign",
            "--state",
            str(state),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["repo"] == "aegis"
    cached = json.loads((state / "cost" / "aegis.json").read_text())
    assert cached["repo"] == "aegis"


def test_usage_repo_refuses_a_path_that_is_not_a_git_repo(tmp_path):
    result = runner.invoke(app, ["repo", str(tmp_path)])

    assert result.exit_code == 2
    assert "not a git repo" in result.output


def test_a_sweep_skips_a_non_git_directory_and_keeps_going(cost_tree, monkeypatch):
    (cost_tree / "repos" / "scratch").mkdir()
    monkeypatch.chdir(cost_tree)

    result = runner.invoke(
        app,
        [
            "repos",
            str(cost_tree / "repos"),
            "--no-foreign",
            "--state",
            str(cost_tree / ".aegis" / "state"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "aegis" in result.output
    assert "une-tools" in result.output
    assert "scratch" not in result.output
    assert "across 2 repos" in result.output


def test_usage_dashboard_still_runs_with_no_subcommand(cost_tree, monkeypatch):
    """The callback has invoke_without_command=True, so a new subcommand must
    not shadow the bare `aegis usage` dashboard."""
    monkeypatch.chdir(cost_tree)
    result = runner.invoke(app, [])

    assert result.exit_code == 0, result.output


def test_a_sweep_inside_an_outer_git_repo_still_skips_a_non_git_child(
    cost_tree, monkeypatch
):
    """The `.git` check, not the exception handler, is what protects this. With
    an outer repo above the sweep directory, `git -C scratch log` succeeds
    against the outer repo and scratch would appear as a row carrying somebody
    else's commits."""
    import subprocess

    subprocess.run(
        ("git", "init", "-q", "-b", "main"),
        cwd=cost_tree,
        check=True,
        capture_output=True,
    )
    (cost_tree / "repos" / "scratch").mkdir()
    monkeypatch.chdir(cost_tree)

    result = runner.invoke(
        app,
        [
            "repos",
            str(cost_tree / "repos"),
            "--no-foreign",
            "--state",
            str(cost_tree / ".aegis" / "state"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "scratch" not in result.output
    assert "across 2 repos" in result.output


def test_an_unpriceable_session_is_declared_not_swallowed(cost_tree, monkeypatch):
    """A session whose model the registry has no rate for must show up as
    unpriced work. Charging it zero would make real work look free, which is
    the same failure mode as an uncovered commit window."""
    import json as _json

    sessions = cost_tree / ".aegis" / "state" / "sessions"
    sessions.joinpath("opencode.jsonl").write_text(
        "\n".join(
            _json.dumps({"v": 1, "aegis_ts": ts, "event": event})
            for ts, event in [
                (
                    "2026-06-02T13:00:00.000000Z",
                    {
                        "t": "SessionMeta",
                        "handle": "opencode",
                        "provider": "opencode",
                        "cwd": str(cost_tree / "repos" / "aegis"),
                    },
                ),
                ("2026-06-02T13:00:01.000000Z", {"t": "SystemInit", "model": "OpenCode"}),
                (
                    "2026-06-02T13:05:00.000000Z",
                    {
                        "t": "Result",
                        "duration_ms": 1000,
                        "is_error": False,
                        "usage": {
                            "input": 500,
                            "cache_creation": 0,
                            "cache_read": 5000,
                            "output": 100,
                        },
                    },
                ),
            ]
        )
        + "\n"
    )
    monkeypatch.chdir(cost_tree)

    result = runner.invoke(
        app,
        [
            "repo",
            str(cost_tree / "repos" / "aegis"),
            "--no-foreign",
            "--state",
            str(cost_tree / ".aegis" / "state"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "unpriced work (counted, not charged)" in result.output
    assert "1.0 sessions" in result.output
