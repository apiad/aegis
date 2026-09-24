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
    # The raw token count, not millions. This line exists to say how much work
    # is unaccounted for, and the counts are small by construction: rounding
    # 5,600 tokens to "0 M" makes the line say nothing.
    assert "5,600 tokens" in result.output
    assert "0 M tokens" not in result.output


def test_a_zero_denominator_prints_na_not_a_number_nine_orders_out(
    cost_tree, monkeypatch
):
    """max(hours, 1e-9) turns a division by zero into 5,000,000,000.00. A narrow
    window or a prose-only repo hits it, and a unit cost that wrong is worse
    than no unit cost."""
    import json as _json

    sessions = cost_tree / ".aegis" / "state" / "sessions"
    # One call, so no gap between calls exists and assisted hours are zero.
    sessions.joinpath("single.jsonl").write_text(
        "\n".join(
            _json.dumps({"v": 1, "aegis_ts": ts, "event": event})
            for ts, event in [
                ("2026-06-04T09:00:00.000000Z",
                 {"t": "SessionMeta", "handle": "single", "provider": "claude-code",
                  "cwd": str(cost_tree / "repos" / "aegis")}),
                ("2026-06-04T09:00:01.000000Z",
                 {"t": "SystemInit", "model": "claude-opus-4-7"}),
                ("2026-06-04T09:00:02.000000Z",
                 {"t": "AssistantText", "text": "x", "message_id": "solo",
                  "usage": {"input": 1000, "cache_creation": 0, "cache_read": 0,
                            "output": 0}}),
            ]
        )
        + "\n"
    )
    monkeypatch.chdir(cost_tree)

    result = runner.invoke(
        app,
        ["repo", str(cost_tree / "repos" / "aegis"), "--no-foreign",
         "--since", "2026-06-04",
         "--state", str(cost_tree / ".aegis" / "state")],
    )

    assert result.exit_code == 0, result.output
    assert "per assisted hour     n/a" in result.output
    assert "per 1k code lines     n/a" in result.output
    assert "5,000,000,000" not in result.output
    # Raw tokens on the headline too: "0 M" next to a real dollar figure is a
    # report contradicting itself.
    assert "1,000 tokens" in result.output


def test_a_state_dir_with_no_transcripts_says_so_instead_of_reporting_zero(
    cost_tree, monkeypatch, tmp_path
):
    """The target is named by absolute path, so nothing signals that the answer
    came from a store chosen by the shell's cwd. An empty store must not look
    like a free repo."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app,
        ["repo", str(cost_tree / "repos" / "aegis"), "--no-foreign",
         "--state", str(tmp_path / "empty-state")],
    )

    assert result.exit_code == 0, result.output
    assert "no transcripts found" in result.output


def test_the_cache_is_written_only_when_json_is_asked_for(cost_tree, monkeypatch):
    """The docs and the spec both say --json caches. Writing on every run means a
    windowed table run silently replaces the figure the MCP tool serves."""
    monkeypatch.chdir(cost_tree)
    state = cost_tree / ".aegis" / "state"
    args = ["repo", str(cost_tree / "repos" / "aegis"), "--no-foreign",
            "--state", str(state)]

    assert runner.invoke(app, args).exit_code == 0
    assert not (state / "cost" / "aegis.json").exists()

    assert runner.invoke(app, [*args, "--json"]).exit_code == 0
    assert (state / "cost" / "aegis.json").exists()


def test_a_sweep_reports_unpriced_work_instead_of_a_silent_zero(
    cost_tree, monkeypatch
):
    """`aegis usage repos` is the command built for cross-repo comparison. A repo
    whose sessions have no rate must not appear as 0.00 with nothing said, which
    is trap 6 reintroduced one command over."""
    import json as _json

    sessions = cost_tree / ".aegis" / "state" / "sessions"
    sessions.joinpath("oc.jsonl").write_text(
        "\n".join(
            _json.dumps({"v": 1, "aegis_ts": ts, "event": event})
            for ts, event in [
                ("2026-06-05T09:00:00.000000Z",
                 {"t": "SessionMeta", "handle": "oc", "provider": "opencode",
                  "cwd": str(cost_tree / "repos" / "une-tools")}),
                ("2026-06-05T09:00:01.000000Z",
                 {"t": "SystemInit", "model": "OpenCode"}),
                ("2026-06-05T09:05:00.000000Z",
                 {"t": "Result", "duration_ms": 1000, "is_error": False,
                  "usage": {"input": 1000, "cache_creation": 0,
                            "cache_read": 14_000_000, "output": 200}}),
            ]
        )
        + "\n"
    )
    monkeypatch.chdir(cost_tree)

    result = runner.invoke(
        app,
        ["repos", str(cost_tree / "repos"), "--no-foreign",
         "--state", str(cost_tree / ".aegis" / "state")],
    )

    assert result.exit_code == 0, result.output
    assert "unpriced" in result.output.lower()
    assert "14,001,200" in result.output


def test_sweep_json_does_not_present_uncomputed_fields_as_zero(
    cost_tree, monkeypatch
):
    """A consumer reading strict_usd: 0.0 concludes the strict attribution is
    zero, which is the error bar the whole measurement exists to publish."""
    monkeypatch.chdir(cost_tree)

    result = runner.invoke(
        app,
        ["repos", str(cost_tree / "repos"), "--no-foreign",
         "--state", str(cost_tree / ".aegis" / "state"), "--json"],
    )

    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert rows and rows[0]["strict_usd"] is None
    assert rows[0]["workspace_usd"] is None
    assert rows[0]["bands"] is None
    assert rows[0]["cost_usd"] is not None
