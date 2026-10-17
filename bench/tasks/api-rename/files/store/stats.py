from .db import TABLES
from .query import count_rows, fetch_rows


def summary(conn) -> dict[str, int]:
    out = {table: count_rows(conn, table) for table in TABLES}
    out["copies"] = sum(r["copies"] for r in fetch_rows(conn, "books"))
    out["on_loan"] = count_rows(conn, "loans", where={"returned": None})
    return out
