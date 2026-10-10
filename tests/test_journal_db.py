import sqlite3
import threading

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


def test_clear_rolls_back_on_error(tmp_path):
    c, _ = db.connect(tmp_path / "clear_test.db")
    db.insert(c, entry(1))
    locker = sqlite3.connect(tmp_path / "clear_test.db")
    locker.execute("BEGIN IMMEDIATE")
    lock_con = sqlite3.connect(
        tmp_path / "clear_test.db", timeout=0.1, isolation_level=None
    )
    lock_con.execute("PRAGMA busy_timeout=100")
    try:
        db.clear(lock_con)
        assert False, "Expected database locked error"
    except sqlite3.OperationalError:
        pass
    locker.execute("ROLLBACK")
    locker.close()
    lock_con.close()
    assert db.insert(c, entry(2)) is True


def test_concurrent_connect_calls_do_not_race(tmp_path):
    db_path = tmp_path / "concurrent_test.db"
    results = []

    def connect_thread():
        try:
            con, fresh = db.connect(db_path)
            con.close()
            results.append(True)
        except Exception as e:
            results.append(e)

    threads = [threading.Thread(target=connect_thread) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(r is True for r in results), f"Some threads failed: {results}"
    con, _ = db.connect(db_path)
    con.close()


def test_path_prefix_match_is_case_sensitive(con):
    db.insert(con, entry(1, touches=[("edit", "SRC/client/a.js")], ts=100))
    db.insert(con, entry(2, touches=[("edit", "src/client/b.js")], ts=200))
    q = db.Query
    assert len(db.search(con, q(path="/r/SRC/client"))[0]) == 1
    assert len(db.search(con, q(path="/r/src/client"))[0]) == 1
    assert db.search(con, q(path="/r/SRC"))[0][0].log_id == "L1"
    assert len(db.search(con, q(path="/r/src"))[0]) == 1


def test_paths_with_special_chars_still_match_prefixes(con):
    db.insert(con, entry(1, touches=[("edit", "src/a%b.js")], ts=100))
    db.insert(con, entry(2, touches=[("edit", "src/a_c.py")], ts=200))
    q = db.Query
    assert len(db.search(con, q(path="/r/src"))[0]) == 2
    assert db.search(con, q(path="/r/src/a%b.js"))[0][0].log_id == "L1"


def test_only_fts5_syntax_errors_raise_bad_pattern(con):
    db.insert(con, entry(1))
    with pytest.raises(db.BadPattern):
        db.search(con, db.Query(pattern='"unclosed'))
    with pytest.raises(db.BadPattern):
        db.search(con, db.Query(pattern="AND"))
    db.search(con, db.Query(pattern="feat"))
    db.search(con, db.Query(pattern='feat OR "fix"'))
