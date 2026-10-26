"""The usage query over hand-built rows."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from aegis.journal import db
from aegis.usage import tables
from aegis.usage.query import UsageQuery, run
from aegis.usage.turns import Derived, PartRow, TurnRow

T0 = datetime(2026, 7, 5, 23, 30, tzinfo=timezone.utc).timestamp()  # a Sunday


def turn(rec, ts=T0, person="ana", cost="1", kind="billed", mode="person"):
    return TurnRow(rec, ts, person, "foreground", "claude-code", "claude-opus-5-5", mode,
                   "api", Decimal(cost), kind, 0, 0, 0, 0, 10.0, 2, 1, 1, 0, 0, None)  # fmt: skip


def part(rec, n, repo, cost, purpose="build", ts=T0, tokens=100):
    return PartRow(
        rec, n, ts, repo, purpose, "editing", True, Decimal(cost), tokens, 5.0
    )


@pytest.fixture
def con(tmp_path):
    c, _ = db.connect(tmp_path / "journal.db")
    tables.ensure(c)
    c.execute(
        "INSERT INTO repo_owners VALUES('/w/atlas', 'acme-co', 'acme-co/atlas', 0)"
    )
    c.execute(
        "INSERT INTO repo_owners VALUES('/w/ledger', 'acme-co', 'acme-co/ledger', 0)"
    )
    c.execute("INSERT INTO repo_owners VALUES('/w/notes', 'ana', 'ana/notes', 0)")
    tables.insert(c, "s1", Derived(
        turns=[turn(1, cost="0.6"), turn(2, cost="0.4", kind="priced")],
        parts=[part(1, 0, "/w/atlas", "0.2"), part(1, 1, "/w/ledger", "0.4", "fix"),
               part(2, 0, "/w/atlas", "0.4")],
    ))  # fmt: skip
    tables.insert(c, "s2", Derived(
        turns=[turn(1, person="ben", cost="1", mode="queue")],
        parts=[part(1, 0, "/w/notes", "1")],
    ))  # fmt: skip
    return c


def q(**kw):
    return UsageQuery(since=T0 - 3600, until=T0 + 3600, **kw)


def test_grouping_by_owner_sums_parts_cost(con):
    a = run(con, q(split=("owner",)))
    assert {r["owner"]: r["cost"] for r in a.rows} == {"acme-co": 1.0, "ana": 1.0}
    assert a.totals["cost"] == 2.0 and a.totals["turns"] == 3


def test_a_session_counts_once_in_a_group(con):
    a = run(con, q(split=("owner",)))
    acme = [r for r in a.rows if r["owner"] == "acme-co"][0]
    assert (acme["sessions"], acme["turns"]) == (1, 2)


def test_dow_hour_buckets_in_the_given_zone(con):
    a = run(con, q(by="dow_hour", tz="Europe/Madrid"))
    assert [r["bucket"] for r in a.rows] == ["Mon 01"]
    a = run(con, q(by="dow_hour"))
    assert [r["bucket"] for r in a.rows] == ["Sun 23"]
    assert [r["bucket"] for r in run(con, q(by="day", tz="Europe/Madrid")).rows] == [
        "2026-07-06"
    ]
    assert [r["bucket"] for r in run(con, q(by="week")).rows] == ["2026-06-29"]


def test_people_filter_by_person(con):
    a = run(con, q(people={"ben"}))
    assert a.totals["cost"] == 1.0 and a.totals["sessions"] == 1


def test_where_filters_a_dimension(con):
    assert run(con, q(where={"purpose": "fix"})).totals["cost"] == 0.4


def test_outcomes_count_journal_rows_by_repo(con):
    for k, (kind, text, repo) in enumerate([
        ("commit", "1a2b3c4 fix: y · main", "/w/atlas"),
        ("pr", "opened https://github.com/acme-co/atlas/pull/3", "/w/atlas"),
        ("pr", "merged #3", "/w/atlas"),
        ("issue", "closed #7", "/w/atlas"),
        ("commit", "5d6e7f8 docs: z · main", "/w/notes"),
        ("turn", "a turn", "/w/notes"),
    ]):  # fmt: skip
        db.insert(con, db.Entry("s1", 10 + k, 0, T0, "h", repo, kind, "", text, None,
                                False, []))  # fmt: skip
    a = run(con, q(split=("repo",)))
    by = {o["repo"]: o for o in a.outcomes}
    assert by["/w/atlas"] == {"repo": "/w/atlas", "commits": 1, "prs_opened": 1,
                              "prs_merged": 1, "issues": 1}  # fmt: skip
    assert by["/w/notes"]["commits"] == 1 and by["/w/notes"]["prs_merged"] == 0
    assert run(con, q(people={"ben"})).outcomes == []


def test_estimated_is_the_share_of_priced_cost(con):
    a = run(con, q())
    assert a.estimated == 0.2 and a.unpriced_tokens == 0


def test_config_owners_name_a_local_repo(con):
    tables.insert(con, "s3", Derived(
        turns=[turn(1, cost="0.5")], parts=[part(1, 0, "/w/site", "0.5")]))  # fmt: skip
    a = run(con, q(split=("owner",)), owners={"site": "acme-co"})
    assert {r["owner"]: r["cost"] for r in a.rows}["acme-co"] == 1.5
    a = run(con, q(split=("owner",)))
    assert {r["owner"]: r["cost"] for r in a.rows}["local"] == 0.5
