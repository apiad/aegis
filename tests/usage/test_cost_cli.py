import json

from typer.testing import CliRunner

from aegis.cli import app

from .conftest import INIT, said
from .stores import opencode_step, opencode_store, store

runner = CliRunner()


def test_usage_repo_prints_a_table_with_the_error_bar(cost_tree, monkeypatch):
    monkeypatch.chdir(cost_tree)
    result = runner.invoke(
        app,
        [
            "usage",
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


def test_usage_repo_refuses_a_path_that_is_not_a_git_repo(tmp_path):
    result = runner.invoke(app, ["usage", "repo", str(tmp_path)])

    assert result.exit_code == 2
    assert "not a git repo" in result.output


def test_a_sweep_skips_a_non_git_directory_and_keeps_going(cost_tree, monkeypatch):
    (cost_tree / "repos" / "scratch").mkdir()
    monkeypatch.chdir(cost_tree)

    result = runner.invoke(
        app,
        [
            "usage",
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
    result = runner.invoke(
        app,
        [
            "usage",
        ],
    )

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
            "usage",
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
    """A session whose model has no price here (an OpenCode one) must show up
    as unpriced work. Charging it zero would make real work look free, which is
    the same failure mode as an uncovered commit window."""
    opencode_store(
        cost_tree / ".aegis" / "state",
        "20260602-130000-oooooo",
        cost_tree / "repos" / "aegis",
        "2026-06-02T13:05:00Z",
        opencode_step("prt_1", inp=500, out=100, read=5000),
    )
    monkeypatch.chdir(cost_tree)

    result = runner.invoke(
        app,
        [
            "usage",
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
    # One call, so no gap between calls exists and assisted hours are zero.
    solo = said("solo", "x", 1000)
    solo["message"]["usage"] = {"input_tokens": 1000}
    store(
        cost_tree / ".aegis" / "state",
        "20260604-090000-ssssss",
        cost_tree / "repos" / "aegis",
        [("2026-06-04T09:00:01Z", INIT), ("2026-06-04T09:00:02Z", solo)],
    )
    monkeypatch.chdir(cost_tree)

    result = runner.invoke(
        app,
        [
            "usage",
            "repo",
            str(cost_tree / "repos" / "aegis"),
            "--no-foreign",
            "--since",
            "2026-06-04",
            "--state",
            str(cost_tree / ".aegis" / "state"),
        ],
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
        [
            "usage",
            "repo",
            str(cost_tree / "repos" / "aegis"),
            "--no-foreign",
            "--state",
            str(tmp_path / "empty-state"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "no transcripts found" in result.output


def test_a_sweep_reports_unpriced_work_instead_of_a_silent_zero(cost_tree, monkeypatch):
    """`aegis usage repos` is the command built for cross-repo comparison. A repo
    whose sessions have no rate must not appear as 0.00 with nothing said, which
    is trap 6 reintroduced one command over."""
    opencode_store(
        cost_tree / ".aegis" / "state",
        "20260605-090000-oooooo",
        cost_tree / "repos" / "une-tools",
        "2026-06-05T09:05:00Z",
        opencode_step("prt_1", inp=1000, out=200, read=14_000_000),
    )
    monkeypatch.chdir(cost_tree)

    result = runner.invoke(
        app,
        [
            "usage",
            "repos",
            str(cost_tree / "repos"),
            "--no-foreign",
            "--state",
            str(cost_tree / ".aegis" / "state"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "unpriced" in result.output.lower()
    assert "14,001,200" in result.output


def test_sweep_json_does_not_present_uncomputed_fields_as_zero(cost_tree, monkeypatch):
    """A consumer reading strict_usd: 0.0 concludes the strict attribution is
    zero, which is the error bar the whole measurement exists to publish."""
    monkeypatch.chdir(cost_tree)

    result = runner.invoke(
        app,
        [
            "usage",
            "repos",
            str(cost_tree / "repos"),
            "--no-foreign",
            "--state",
            str(cost_tree / ".aegis" / "state"),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert rows and rows[0]["strict_usd"] is None
    assert rows[0]["workspace_usd"] is None
    assert rows[0]["bands"] is None
    assert rows[0]["cost_usd"] is not None
