"""The usage tables: created beside the journal's, idempotent, settled."""

from decimal import Decimal

from aegis.journal import db
from aegis.usage import tables
from aegis.usage.turns import Derived, PartRow, Settle, TurnRow, WaitRow

COLUMNS = {
    "usage_turns": "log_id rec ts person kind harness model mode billing cost cost_kind"
    " tok_in tok_out tok_cw tok_cr secs calls errors exits ctx_start ctx_peak continues",
    "usage_parts": "log_id rec n ts repo purpose activity settled cost tokens secs",
    "usage_waits": "log_id rec ts woke_by secs",
    "usage_ctx": "log_id rec band calls cost tok_cr",
    "usage_whatif": "log_id rec at handoffs saved rewrite",
    "usage_quota": "billing window ts percent resets_at",
    "repo_owners": "repo owner remote checked_at",
    "usage_meta": "key value",
}


def _derived() -> Derived:
    turn = TurnRow(4, 1000.0, "ana", "foreground", "claude-code", "claude-opus-5-5",
                   "person", "api", Decimal("0.3"), "billed", 1, 2, 3, 4, 5.0, 2, 0, 0,
                   10, 20, None)  # fmt: skip
    parts = [
        PartRow(
            4, 0, 1000.0, "/w/atlas", "build", "editing", False, Decimal("0.2"), 6, 3.0
        ),
        PartRow(
            4, 1, 1000.0, "/w/atlas", "chat", "reading", True, Decimal("0.1"), 4, 2.0
        ),
    ]
    return Derived(turns=[turn], parts=parts, waits=[WaitRow(2, 990.0, "person", 30.0)])


def test_ensure_creates_every_table_with_its_columns(tmp_path):
    con, _ = db.connect(tmp_path / "journal.db")
    assert tables.ensure(con) is True
    assert tables.ensure(con) is False
    for name, cols in COLUMNS.items():
        got = [r[1] for r in con.execute(f"PRAGMA table_info({name})")]
        assert got == cols.split(), name


def test_an_insert_is_idempotent_and_a_settle_fixes_the_purpose(tmp_path):
    con, _ = db.connect(tmp_path / "journal.db")
    tables.ensure(con)
    assert tables.insert(con, "s1", _derived()) == 4
    assert tables.insert(con, "s1", _derived()) == 0
    tables.insert(con, "s1", Derived(settles=[Settle(4, 0, "fix")]))
    assert con.execute(
        "SELECT purpose, settled, cost FROM usage_parts WHERE rec = 4 AND n = 0"
    ).fetchone() == ("fix", 1, 0.2)
    # A repeat of the original rows does not unsettle it.
    tables.insert(con, "s1", _derived())
    assert con.execute(
        "SELECT purpose, settled FROM usage_parts WHERE n = 0"
    ).fetchone() == ("fix", 1)


def test_a_version_bump_recreates_only_the_usage_tables(tmp_path, monkeypatch):
    con, _ = db.connect(tmp_path / "journal.db")
    db.insert(
        con, db.Entry("s1", 1, 0, 1.0, "h", None, "turn", "", "x", None, False, [])
    )
    tables.ensure(con)
    tables.insert(con, "s1", _derived())
    monkeypatch.setattr(tables, "USAGE_VERSION", 2)
    assert tables.ensure(con) is True
    assert con.execute("SELECT count(*) FROM usage_turns").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM entries").fetchone()[0] == 1


def test_the_backfill_mark_completes_only_for_its_own_token(tmp_path):
    con, _ = db.connect(tmp_path / "journal.db")
    tables.ensure(con)
    assert not tables.is_complete(con)
    tables.mark(con, 7)
    tables.clear(con, 9)  # a rebuild began meanwhile
    tables.complete_if(con, 7)
    assert not tables.is_complete(con)
    tables.complete_if(con, 9)
    assert tables.is_complete(con)
