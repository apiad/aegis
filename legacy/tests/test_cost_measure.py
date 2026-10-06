import json

import pytest

from aegis.cost.measure import CostOptions, measure


def _options(tree) -> CostOptions:
    return CostOptions(
        state_dir=tree / ".aegis" / "state",
        foreign=False,
        home_projects=tree / "no-such-projects",
    )


def test_proportional_and_strict_attribution_bracket_the_answer(cost_tree):
    result = measure(cost_tree / "repos" / "aegis", _options(cost_tree))

    # `inside` contributes whole. `mixed` has two records naming aegis and one
    # naming une-tools, so it contributes 2/3 of its own cost: locality counts
    # records, not path occurrences, and m2 names two aegis paths in one record.
    assert result.strict_usd < result.cost_usd
    assert result.cost_usd < result.workspace_usd
    assert result.bands["0 (no mention)"]["sessions"] == 1
    assert result.bands["1.0 (this repo only)"]["sessions"] == 1
    assert result.bands["0.1-0.8 (mixed)"]["sessions"] == 1
    assert result.bands["0.1-0.8 (mixed)"]["attributed"] == pytest.approx(
        result.bands["0.1-0.8 (mixed)"]["cost"] * 2 / 3, rel=1e-6
    )


def test_to_dict_is_json_serialisable_and_drops_the_date_list(cost_tree):
    result = measure(cost_tree / "repos" / "aegis", _options(cost_tree))
    data = result.to_dict()

    text = json.dumps(data)  # raises if anything is not serialisable
    assert "dates" not in data["git"]
    assert data["repo"] == "aegis"
    # Trap 4, showing up in the fixture on its own: the only commit is dated
    # 2026-06-01 and the oldest transcript 2026-06-02, so no commit falls
    # inside the measured window and coverage is 0.
    assert data["coverage"] == 0.0
    assert data["first_seen"].startswith("2026-06-02")
    assert set(data["bands"]) == {
        "0 (no mention)",
        "<0.1 (passing mention)",
        "0.1-0.8 (mixed)",
        "0.8-0.95 (almost only this repo)",
        "1.0 (this repo only)",
    }
    assert json.loads(text)["path"].endswith("/repos/aegis")


def test_a_repo_with_no_commits_in_the_window_measures_without_raising(cost_tree):
    options = _options(cost_tree)
    options.since = "2027-01-01"

    result = measure(cost_tree / "repos" / "aegis", options)

    assert result.git.n_commits == 0
    assert result.cost_usd == 0.0
    assert result.coverage == 1.0
    assert json.dumps(result.to_dict())


def test_no_foreign_keeps_an_explicit_extra_root(cost_tree):
    """--no-foreign skips ~/.claude/projects, which aegis does not own. An
    --extra-root the user named on the command line is not that: someone
    measuring only a synced host's transcripts would otherwise get 0.00 USD and
    exit 0."""
    extra = cost_tree / "synced" / "-home-apiad-Workspace"
    extra.mkdir(parents=True)
    (extra / "s9.jsonl").write_text(
        json.dumps(
            {
                "timestamp": "2026-06-03T12:00:00.000Z",
                "type": "assistant",
                "sessionId": "s9",
                "cwd": str(cost_tree / "repos" / "aegis"),
                "message": {
                    "id": "msg_extra",
                    "model": "claude-opus-4-7",
                    "usage": {"input_tokens": 1_000_000, "output_tokens": 0},
                },
            }
        )
        + "\n"
    )
    options = _options(cost_tree)
    options.extra_roots = (extra,)
    options.state_dir = None  # only the extra root

    result = measure(cost_tree / "repos" / "aegis", options)

    assert result.cost_usd > 0
    assert result.sessions == 1.0


def test_a_repo_outside_a_directory_named_repos_is_still_attributed(tmp_path):
    """The mention pattern is derived from the repo's own parent directory, not
    from a literal `repos/`. aegis ships on PyPI and takes a path precisely
    because it has no repos/ convention; hard-coding one in the attribution
    engine turns every mention-only session into share 0 for anyone with a
    different layout, and the output looks plausible."""
    import subprocess

    base = tmp_path / "projects"
    repo = base / "widget"
    repo.mkdir(parents=True)
    subprocess.run(("git", "init", "-q", "-b", "main"), cwd=repo, check=True,
                   capture_output=True)
    for key, value in (("user.email", "t@example.com"), ("user.name", "Tester")):
        subprocess.run(("git", "config", key, value), cwd=repo, check=True,
                       capture_output=True)
    (repo / "src").mkdir()
    (repo / "src" / "main.py").write_text("a = 1\n")
    from tests.conftest import _cost_commit

    _cost_commit(repo, "feat: start", ("src/main.py",), "2026-06-01")

    sessions = tmp_path / ".aegis" / "state" / "sessions"
    sessions.mkdir(parents=True)
    # Working elsewhere, talking only about the repo by its relative path.
    sessions.joinpath("mentions.jsonl").write_text(
        "\n".join([
            json.dumps({"v": 1, "aegis_ts": "2026-06-02T10:00:00.000000Z",
                        "event": {"t": "SessionMeta", "handle": "mentions",
                                  "provider": "claude-code", "cwd": str(tmp_path)}}),
            json.dumps({"v": 1, "aegis_ts": "2026-06-02T10:00:01.000000Z",
                        "event": {"t": "SystemInit", "model": "claude-opus-4-7"}}),
            json.dumps({"v": 1, "aegis_ts": "2026-06-02T10:00:02.000000Z",
                        "event": {"t": "AssistantText",
                                  "text": "editing projects/widget/src/main.py",
                                  "message_id": "mw",
                                  "usage": {"input": 1_000_000, "cache_creation": 0,
                                            "cache_read": 0, "output": 0}}}),
        ]) + "\n"
    )

    options = CostOptions(
        state_dir=tmp_path / ".aegis" / "state",
        foreign=False,
        home_projects=tmp_path / "no-such-projects",
    )
    result = measure(repo, options)

    assert result.cost_usd > 0
    assert result.sessions == 1.0
