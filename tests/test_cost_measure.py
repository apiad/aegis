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
