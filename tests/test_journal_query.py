import datetime as dt
import time

import pytest

from aegis.journal import db, query, render, when

NOON = time.mktime(dt.datetime(2026, 10, 10, 12, 0).timetuple())
MIDNIGHT = time.mktime(dt.date(2026, 10, 10).timetuple())


def test_days_are_local_and_until_ends_its_day():
    assert when.bound("2026-10-10", end=False, now=NOON) == MIDNIGHT
    assert when.bound("2026-10-10", end=True, now=NOON) == MIDNIGHT + 86400
    assert when.bound("today", end=False, now=NOON) == MIDNIGHT
    assert when.bound("yesterday", end=True, now=NOON) == MIDNIGHT
    assert when.bound("7d", end=False, now=NOON) == NOON - 7 * 86400
    assert when.bound(None, end=False) is None
    with pytest.raises(ValueError, match="not a day"):
        when.bound("tuesday", end=False)


def test_a_loose_path_query_names_the_same_prefix(tmp_path):
    """Review focus 5: trailing slash, .., a file."""
    root = tmp_path.resolve()
    (root / "src" / "client").mkdir(parents=True)
    con, _ = db.connect(root / "j.db")
    clean = query.build(con, str(root), path="src/client").path
    assert clean == str(root / "src" / "client")
    assert query.build(con, str(root), path="src/client/").path == clean
    assert query.build(con, str(root), path="src/x/../client").path == clean
    assert query.build(con, str(root), path=str(root / "src/client")).path == clean


def test_text_groups_by_day_and_says_when_cut():
    hits = [
        db.Hit(
            1,
            NOON,
            "L",
            "calm-hopper",
            "commit",
            "",
            "1a2b3c4 feat: x · main",
            "t1",
            "/r",
            False,
            ["/root/repos/a/x.py", "/root/repos/a/y.py", "/a", "/b"],
        )
    ]
    out = render.text(hits, True, "/root")
    assert out.splitlines()[0].startswith("2026-10-10")
    assert "12:00  calm-hopper  commit  1a2b3c4 feat: x · main" in out
    assert "repos/a/x.py  repos/a/y.py  /a  +1 more" in out
    assert out.endswith("more match: narrow the filters or raise limit")


def test_rows_say_whether_the_session_is_open():
    hits = [
        db.Hit(
            1, NOON, "L", "h", "note", "decision", "chose sqlite", "t9", None, False, []
        )
    ]
    [row] = render.rows(hits, "/root", {"L"})
    assert (
        row["open"]
        and row["glyph"] == "✎"
        and row["source"] == "t9"
        and row["time"] == "12:00"
    )
