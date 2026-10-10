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
    with pytest.raises(ValueError, match="not a day"):
        when.bound("9999-12-31", end=True)


def test_a_loose_path_query_names_the_same_prefix(tmp_path):
    """A path query names the same prefix whether it has a trailing slash, a .., or is absolute."""
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


def test_a_path_query_in_a_worktree_names_the_main_checkout(tmp_path):
    """A path query inside a git repo with a worktree names the main checkout."""
    import subprocess

    root = tmp_path.resolve()
    subprocess.run(
        ["git", "init", "-q"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "test"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    (root / "src").mkdir()
    wt_path = root / ".claude" / "worktrees" / "t"
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", "topic", str(wt_path)],
        cwd=root,
        check=True,
        capture_output=True,
    )
    con, _ = db.connect(root / "j.db")
    worktree_result = query.build(con, str(root), path=str(wt_path / "src")).path
    main_result = query.build(con, str(root), path=str(root / "src")).path
    assert worktree_result == main_result == str(root / "src")


def test_empty_text_renders_zero_entries():
    """render.text with no hits returns zero entries."""
    out = render.text([], False, "/root")
    assert out == "0 entries"


def _put(
    con,
    rec,
    *,
    log_id="L1",
    handle="rustic-rivest",
    kind="note",
    text="x",
    ts=None,
    paths=(),
):
    db.insert(
        con,
        db.Entry(
            log_id,
            rec,
            0,
            NOON + rec if ts is None else ts,
            handle,
            None,
            kind,
            "",
            text,
            None,
            False,
            [("edit", None, p, p) for p in paths],
        ),
    )


def test_the_fuzzy_box_reads_old_handles_paths_and_kinds(tmp_path):
    con, _ = db.connect(tmp_path / "j.db")
    root = "/root"
    _put(con, 1, kind="session", text="spawned in /root")
    _put(
        con,
        2,
        handle="calm-hopper",
        kind="session",
        text="renamed from rustic-rivest to calm-hopper",
    )
    _put(
        con,
        3,
        handle="calm-hopper",
        kind="commit",
        text="1a2b3c4 fix · main",
        paths=["/root/scratch-repo/notes.md"],
    )
    _put(
        con,
        4,
        log_id="L2",
        handle="other-one",
        kind="commit",
        text="9f9f9f9 y · main",
        paths=["/elsewhere/notes.md"],
    )

    def texts(q):
        return [h.text for h, _ in query.matching(con, db.Query(), q, root)]

    # The commit was made as calm-hopper; the session was rustic-rivest first.
    assert "1a2b3c4 fix · main" in texts("rustic cmt")
    assert texts("Rustic cmt") == texts("rustic cmt") == ["1a2b3c4 fix · main"]
    assert texts("cmt notes") == ["9f9f9f9 y · main", "1a2b3c4 fix · main"]
    assert texts("scra") == ["1a2b3c4 fix · main"]
    assert texts("scra other") == []
    # Paths are matched as shown: relative under the root, else absolute.
    assert texts("root/scra") == []
    assert texts("elsewhere") == ["9f9f9f9 y · main"]
    [(_, marks)] = list(query.matching(con, db.Query(), "fix scra", root))
    assert marks == [8, 9, 10]


def test_the_fuzzy_walk_goes_on_past_a_batch_newest_first(tmp_path, monkeypatch):
    monkeypatch.setattr(query, "BATCH", 2)
    con, _ = db.connect(tmp_path / "j.db")
    for i in range(7):
        _put(con, i, text=f"entry {i}", ts=NOON)  # one ts: the id breaks ties
    _put(con, 7, text="other")
    got = [h.text for h, _ in query.matching(con, db.Query(kinds=["note"]), "ent", "/")]
    assert got == [f"entry {i}" for i in reversed(range(7))]
