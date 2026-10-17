from .models import Book
from .query import fetch_rows


def search_books(conn, term: str, limit: int = 20) -> list[Book]:
    """Books whose title contains `term`, case-insensitively, then whose author does."""
    pattern = f"%{term}%"
    by_title = fetch_rows(conn, "books", where={"title": ("LIKE", pattern)}, order_by="title", limit=limit)
    seen = {r["id"] for r in by_title}
    by_author = [
        r for r in fetch_rows(conn, "books", where={"author": ("LIKE", pattern)}, order_by="title") if r["id"] not in seen
    ]
    return [Book.from_row(r) for r in (by_title + by_author)[:limit]]
