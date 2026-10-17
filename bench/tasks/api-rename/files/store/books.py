from .models import Book
from .query import fetch_rows


def list_books(conn, author: str | None = None) -> list[Book]:
    where = {"author": author} if author else None
    return [Book.from_row(r) for r in fetch_rows(conn, "books", where=where, order_by="title")]


def get_book(conn, book_id: int) -> Book | None:
    rows = fetch_rows(conn, "books", where={"id": book_id}, limit=1)
    return Book.from_row(rows[0]) if rows else None


def books_since(conn, year: int) -> list[Book]:
    rows = fetch_rows(conn, "books", where={"year": (">=", year)}, order_by="-year")
    return [Book.from_row(r) for r in rows]
