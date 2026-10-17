from collections import Counter

from .query import count_rows, fetch_rows


def popular_books(conn, top: int = 5) -> list[tuple[str, int]]:
    """The `top` most borrowed titles, with how many times each was borrowed."""
    titles = {r["id"]: r["title"] for r in fetch_rows(conn, "books")}
    counts = Counter(r["book_id"] for r in fetch_rows(conn, "loans"))
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], titles[kv[0]]))
    return [(titles[book_id], n) for book_id, n in ranked[:top]]


def member_activity(conn) -> dict[str, int]:
    names = {r["id"]: r["name"] for r in fetch_rows(conn, "members")}
    return {names[mid]: count_rows(conn, "loans", where={"member_id": mid}) for mid in sorted(names, key=names.get)}
