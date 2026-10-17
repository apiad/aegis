from .models import Member
from .query import fetch_rows as _fetch


def list_members(conn) -> list[Member]:
    return [Member.from_row(r) for r in _fetch(conn, "members", order_by="name")]


def find_member(conn, email: str) -> Member | None:
    rows = _fetch(conn, "members", where={"email": email}, limit=1)
    return Member.from_row(rows[0]) if rows else None
