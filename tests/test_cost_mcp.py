import json

from aegis.cost.measure import cache_path
from aegis.mcp.server import repo_cost_payload


def test_repo_cost_tool_reads_the_cache_and_reports_its_age(tmp_path):
    state = tmp_path / "state"
    path = cache_path(state, "aegis")
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "repo": "aegis",
                "cost_usd": 123.45,
                "strict_usd": 120.0,
                "coverage": 0.93,
                "hours": 40.0,
                "generated": "2026-09-24T06:00:00+00:00",
                "modules": {"src": 100.0},
                "git": {"n_commits": 300},
            }
        )
    )

    payload = repo_cost_payload(state, "aegis", now="2026-09-24T12:00:00+00:00")

    assert payload["repo"] == "aegis"
    assert payload["cost_usd"] == 123.45
    assert payload["commits"] == 300
    assert payload["cache_age_hours"] == 6.0


def test_repo_cost_tool_says_where_it_looked_when_there_is_no_cache(tmp_path):
    payload = repo_cost_payload(
        tmp_path / "state", "aegis", now="2026-09-24T12:00:00+00:00"
    )

    assert "error" in payload
    assert "aegis.json" in payload["error"]
    assert "refresh" in payload["error"]
