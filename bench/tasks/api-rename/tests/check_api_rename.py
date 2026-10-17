"""Hidden checks for api-rename, run from WORK with the package importable."""

import subprocess
import sys
import warnings

import pytest

from store.db import connect

COMMANDS = [["books"], ["search", "sea"], ["overdue"], ["popular"], ["stats"], ["check"], ["export", "loans"], ["rows", "books"]]
MODULES = ["books", "members", "loans", "search", "reports", "stats", "export", "importer", "validate", "cache", "cli"]


@pytest.fixture
def conn():
    c = connect()
    c.executemany(
        "INSERT INTO books (id, title, author, year, copies) VALUES (?, ?, ?, ?, ?)",
        [(1, "Glass Orchard", "Ines Vall", 2011, 1), (2, "The Long Thaw", "Omar Field", 2019, 2)],
    )
    c.execute("INSERT INTO members (id, name, email, joined) VALUES (1, 'Sam Ito', 'sam@example.com', '2025-05-05')")
    c.execute("INSERT INTO loans (id, book_id, member_id, out, due) VALUES (1, 2, 1, '2026-01-01', '2026-01-15')")
    c.commit()
    return c


def test_load_records_lives_in_store_records(conn):
    from store.records import load_records

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        rows = load_records(conn, "books", where={"year": (">=", 2015)}, order_by="-year", limit=5)
    assert [r["title"] for r in rows] == ["The Long Thaw"]


def test_fetch_rows_still_works_and_warns(conn):
    from store.records import load_records

    with pytest.warns(DeprecationWarning):
        from store.query import fetch_rows

        rows = fetch_rows(conn, "loans", where={"returned": None})
    assert rows == load_records(conn, "loans", where={"returned": None})


def test_no_module_and_no_command_uses_the_old_name(conn, tmp_path):
    db = tmp_path / "lib.db"
    conn.execute(f"VACUUM INTO '{db}'")
    strict = [sys.executable, "-W", "error::DeprecationWarning"]
    for name in MODULES:
        r = subprocess.run([*strict, "-c", f"import store.{name}"], capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, f"import store.{name}: {r.stderr[-400:]}"
    for cmd in COMMANDS:
        r = subprocess.run([*strict, "-m", "store.cli", str(db), *cmd], capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, f"store {' '.join(cmd)}: {r.stderr[-400:]}"
