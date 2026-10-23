import sqlite3
import time

from aegis.knowledge.service import Knowledge
from aegis.knowledge.usage import Usage

from .knowledge_samples import mem


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
    for i in range(500):
        u.add("ana", "s1", "memory", f"m{i}", "read", "x" * 200, ts=1.0)
    u.prune(now)
    assert set(u.summary("ana")) == {"new"}
    # VACUUM ran: the deleted rows' pages went back to the disk.
    free = sqlite3.connect(u.path).execute("PRAGMA freelist_count").fetchone()[0]
    assert free == 0
    u.stop()


def test_principals_do_not_mix(tmp_path):
    u = Usage(tmp_path / "u.sqlite")
    u.add("ana", "s1", "memory", "x", "read")
    u.add("ben", "s2", "memory", "y", "read")
    assert set(u.summary("ana")) == {"x"} and set(u.summary("ben")) == {"y"}
    assert [r["name"] for r in u.rows("ben")] == ["y"]


async def test_a_locked_usage_log_never_holds_a_hint(tmp_path):
    kn = Knowledge(tmp_path)
    await kn.store.save("ana", mem("bot-host", "the bot runs on host b"), "t")
    other = sqlite3.connect(tmp_path / "knowledge-usage.sqlite", isolation_level=None)
    other.execute("CREATE TABLE IF NOT EXISTS x(a)")
    other.execute("BEGIN IMMEDIATE")  # holds the write lock
    try:
        t0 = time.monotonic()
        block = kn.hint_for("ana", "s1", "deploy the bot", None)
        took = time.monotonic() - t0
    finally:
        other.execute("ROLLBACK")
        other.close()
    assert "bot-host" in block and took < 0.1
    # The writer waited out the lock and wrote the row.
    assert [r["event"] for r in kn.usage.rows("ana")] == ["hinted"]
    kn.usage.stop()


def test_a_sqlite_error_is_logged_and_dropped(tmp_path, caplog):
    u = Usage(tmp_path / "u.sqlite")
    u.add("ana", "s1", "memory", "x", "read")
    u.flush()
    u._q.put(("sql", "INSERT INTO nowhere VALUES (1)", ()))
    u.add("ana", "s1", "memory", "y", "read")
    assert set(u.summary("ana")) == {"x", "y"}
    assert "nowhere" in caplog.text
    u.stop()
