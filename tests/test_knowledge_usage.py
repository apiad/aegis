from aegis.knowledge.usage import Usage


def test_summary_counts_events_per_item(tmp_path):
    u = Usage(tmp_path / "u.sqlite")
    u.add("ana", "s1", "memory", "bot-host", "hinted", "rank=1", ts=10)
    u.add("ana", "s2", "memory", "bot-host", "hinted", "rank=2", ts=20)
    u.add("ana", "s2", "memory", "bot-host", "read", ts=30)
    u.add("ana", "s2", "skill", "release", "invoked", ts=40)
    s = u.summary("ana")
    assert s["bot-host"]["hinted"] == 2 and s["bot-host"]["read"] == 1
    assert s["bot-host"]["last_used"] == 30
    assert s["release"] == {
        "kind": "skill",
        "hinted": 0,
        "read": 0,
        "invoked": 1,
        "ran": 0,
        "last_used": 40,
    }


def test_a_read_after_a_hint_keeps_the_rank_in_detail(tmp_path):
    u = Usage(tmp_path / "u.sqlite")
    u.add("ana", "s1", "memory", "bot-host", "hinted", "rank=2")
    u.add("ana", "s1", "memory", "bot-host", "read", "rank=2")
    assert [(r["event"], r["detail"]) for r in u.rows("ana", "bot-host")] == [
        ("hinted", "rank=2"),
        ("read", "rank=2"),
    ]


def test_prune_drops_rows_older_than_a_year(tmp_path):
    u = Usage(tmp_path / "u.sqlite")
    now = 400 * 86400.0
    u.add("ana", "s1", "memory", "old", "read", ts=now - 366 * 86400)
    u.add("ana", "s1", "memory", "new", "read", ts=now - 364 * 86400)
    assert u.prune(now) == 1
    assert set(u.summary("ana")) == {"new"}


def test_principals_do_not_mix(tmp_path):
    u = Usage(tmp_path / "u.sqlite")
    u.add("ana", "s1", "memory", "x", "read")
    u.add("ben", "s2", "memory", "y", "read")
    assert set(u.summary("ana")) == {"x"} and set(u.summary("ben")) == {"y"}
    assert [r["name"] for r in u.rows("ben")] == ["y"]
