import pytest

from aegis.journal import db


def entry(
    rec, kind="commit", text="feat: x", ts=100.0, touches=(), handle="h", log_id="L1"
):
    return db.Entry(
        log_id,
        rec,
        0,
        ts,
        handle,
        "/r",
        kind,
        "",
        text,
        f"t{rec}",
        False,
        [(op, "/r", p, f"/r/{p}") for op, p in touches],
    )


@pytest.fixture
def con(tmp_path):
    c, fresh = db.connect(tmp_path / "journal.db")
    assert fresh
    return c


def test_an_insert_is_idempotent_by_log_id_record_and_ordinal(con):
    assert db.insert(con, entry(1)) is True
    assert db.insert(con, entry(1, text="other")) is False
    hits, cut = db.search(con, db.Query())
    assert [h.text for h in hits] == ["feat: x"] and not cut


def test_search_filters_by_path_prefix_kind_days_pattern_and_session(con):
    db.insert(con, entry(1, touches=[("edit", "src/client/a.js")], ts=100))
    db.insert(
        con,
        entry(
            2,
            kind="turn",
            text="fixed the find bar",
            touches=[("edit", "src/b.py")],
            ts=200,
        ),
    )
    db.insert(
        con,
        entry(3, kind="note", text="chose sqlite", ts=300, handle="other", log_id="L2"),
    )
    q = db.Query
    assert [h.text for h in db.search(con, q(path="/r/src/client"))[0]] == ["feat: x"]
    assert [h.text for h in db.search(con, q(path="/r/src"))[0]] == [
        "fixed the find bar",
        "feat: x",
    ]
    assert [h.text for h in db.search(con, q(kinds=["note"]))[0]] == ["chose sqlite"]
    assert [h.text for h in db.search(con, q(since=150, until=250))[0]] == [
        "fixed the find bar"
    ]
    assert [h.text for h in db.search(con, q(pattern='"find bar"'))[0]] == [
        "fixed the find bar"
    ]
    assert db.log_ids(con, "other") == ["L2", "other"]
    assert [h.text for h in db.search(con, q(log_ids=db.log_ids(con, "other")))[0]] == [
        "chose sqlite"
    ]


def test_a_hit_carries_its_paths_and_the_limit_reports_a_cut(con):
    for k in range(3):
        db.insert(con, entry(k, touches=[("edit", f"f{k}.py")], ts=k))
    hits, cut = db.search(con, db.Query(limit=2))
    assert cut and [h.paths for h in hits] == [["/r/f2.py"], ["/r/f1.py"]]


def test_a_bad_fts_pattern_raises_bad_pattern(con):
    db.insert(con, entry(1))
    with pytest.raises(db.BadPattern):
        db.search(con, db.Query(pattern='"unclosed'))


def test_counts_by_kind_and_a_schema_change_resets(tmp_path):
    c, _ = db.connect(tmp_path / "j.db")
    db.insert(c, entry(1))
    db.insert(c, entry(2, kind="turn"))
    assert db.counts(c, db.Query()) == {"commit": 1, "turn": 1}
    c.execute("PRAGMA user_version=0")
    c.close()
    c2, fresh = db.connect(tmp_path / "j.db")
    assert fresh and db.search(c2, db.Query())[0] == []
