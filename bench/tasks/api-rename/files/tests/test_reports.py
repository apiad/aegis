import io

from store.cache import cached
from store.export import to_csv
from store.reports import member_activity, popular_books
from store.stats import summary
from store.validate import problems


def test_reports(conn):
    assert popular_books(conn, 2) == [("The Salt Road", 3), ("A Sea of Lamps", 1)]
    assert member_activity(conn) == {"Kim Novak": 3, "Ravi Shah": 2}
    assert summary(conn) == {"books": 4, "members": 2, "loans": 5, "copies": 7, "on_loan": 3}


def test_export_and_check(conn):
    out = io.StringIO()
    assert to_csv(conn, "members", out) == 2
    assert out.getvalue().splitlines()[0] == "id,name,email,joined"
    assert problems(conn) == []


def test_cache_reads_once(conn):
    reader = cached(conn)
    assert reader.count_rows("books") == 4
    conn.execute("DELETE FROM loans")
    conn.execute("DELETE FROM books")
    assert reader.count_rows("books") == 4
