"""Integrity checks the schema cannot express."""

from .query import fetch_rows


def problems(conn) -> list[str]:
    out = []
    books = {r["id"]: r for r in fetch_rows(conn, "books")}
    members = {r["id"] for r in fetch_rows(conn, "members")}
    out_now: dict[int, int] = {}
    for loan in fetch_rows(conn, "loans", order_by="id"):
        if loan["book_id"] not in books:
            out.append(f"loan {loan['id']}: no book {loan['book_id']}")
        if loan["member_id"] not in members:
            out.append(f"loan {loan['id']}: no member {loan['member_id']}")
        if loan["due"] < loan["out"]:
            out.append(f"loan {loan['id']}: due before it went out")
        if loan["returned"] is None:
            out_now[loan["book_id"]] = out_now.get(loan["book_id"], 0) + 1
    for book_id, n in sorted(out_now.items()):
        if book_id in books and n > books[book_id]["copies"]:
            out.append(f"book {book_id}: {n} loans out, {books[book_id]['copies']} copies")
    return out
