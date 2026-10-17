from datetime import date

from store.books import books_since, get_book, list_books
from store.loans import available_copies, overdue
from store.members import find_member
from store.search import search_books


def test_books_by_author(conn):
    assert [b.title for b in list_books(conn, "Ana Ruiz")] == ["A Sea of Lamps", "The Salt Road"]
    assert get_book(conn, 2).author == "Tom Berg"
    assert [b.year for b in books_since(conn, 2004)] == [2021, 2015, 2004]


def test_members_and_loans(conn):
    assert find_member(conn, "ravi@example.com").name == "Ravi Shah"
    assert [loan.id for loan in overdue(conn, date(2026, 2, 16))] == [2]
    assert available_copies(conn, 1) == 1


def test_search_falls_back_to_author(conn):
    assert [b.id for b in search_books(conn, "ruiz")] == [3, 1]
