import pytest

from store.query import QueryError, count_rows, fetch_rows


def test_rows_are_dicts_in_order(conn):
    rows = fetch_rows(conn, "books", order_by="-year", limit=2)
    assert [r["title"] for r in rows] == ["Paper Harbour", "A Sea of Lamps"]


def test_where_operators_and_null(conn):
    assert [r["id"] for r in fetch_rows(conn, "books", where={"year": ("<", 2010)}, order_by="id")] == [1, 2]
    assert count_rows(conn, "loans", where={"returned": None}) == 3
    assert count_rows(conn, "loans", where={"returned": ("!=", None)}) == 2


def test_bad_input_is_refused(conn):
    with pytest.raises(QueryError):
        fetch_rows(conn, "users")
    with pytest.raises(QueryError):
        fetch_rows(conn, "books", where={"year": ("~", 1)})
