from datetime import date

from . import query
from .models import Loan


def active_loans(conn) -> list[Loan]:
    rows = query.fetch_rows(conn, "loans", where={"returned": None}, order_by="due")
    return [Loan.from_row(r) for r in rows]


def overdue(conn, today: date) -> list[Loan]:
    rows = query.fetch_rows(conn, "loans", where={"returned": None, "due": ("<", today.isoformat())}, order_by="due")
    return [Loan.from_row(r) for r in rows]


def loans_of(conn, member_id: int) -> list[Loan]:
    return [Loan.from_row(r) for r in query.fetch_rows(conn, "loans", where={"member_id": member_id}, order_by="out")]


def available_copies(conn, book_id: int) -> int:
    book = query.fetch_rows(conn, "books", where={"id": book_id}, limit=1)
    if not book:
        raise LookupError(f"no book {book_id}")
    return book[0]["copies"] - query.count_rows(conn, "loans", where={"book_id": book_id, "returned": None})
