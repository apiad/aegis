"""Reading rows. fetch_rows is the one way the rest of the package reads a table.

    fetch_rows(conn, "books", where={"author": "Ana Ruiz"}, order_by="year", limit=10)

`where` maps a column to a value (equality) or to a (operator, value) pair, with
the operator one of =, !=, <, <=, >, >=, LIKE. Rows come back as plain dicts.
"""

import sqlite3

from .db import TABLES

OPERATORS = {"=", "!=", "<", "<=", ">", ">=", "LIKE"}


class QueryError(ValueError):
    pass


def _where_clause(where: dict | None) -> tuple[str, list]:
    if not where:
        return "", []
    parts, params = [], []
    for column, cond in where.items():
        if not column.isidentifier():
            raise QueryError(f"fetch_rows: bad column name {column!r}")
        op, value = cond if isinstance(cond, tuple) else ("=", cond)
        if op not in OPERATORS:
            raise QueryError(f"fetch_rows: unknown operator {op!r}")
        if value is None and op in ("=", "!="):
            parts.append(f"{column} IS {'NOT ' if op == '!=' else ''}NULL")
        else:
            parts.append(f"{column} {op} ?")
            params.append(value)
    return " WHERE " + " AND ".join(parts), params


def _check_table(table: str) -> None:
    if table not in TABLES:
        raise QueryError(f"fetch_rows: unknown table {table!r}")


def fetch_rows(
    conn: sqlite3.Connection,
    table: str,
    where: dict | None = None,
    order_by: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Every row of `table` matching `where`, as dicts, in `order_by` order."""
    _check_table(table)
    clause, params = _where_clause(where)
    sql = f"SELECT * FROM {table}{clause}"
    if order_by:
        desc = order_by.startswith("-")
        column = order_by.lstrip("-")
        if not column.isidentifier():
            raise QueryError(f"fetch_rows: bad order_by {order_by!r}")
        sql += f" ORDER BY {column}{' DESC' if desc else ''}"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    return [dict(r) for r in conn.execute(sql, params)]


def count_rows(conn: sqlite3.Connection, table: str, where: dict | None = None) -> int:
    _check_table(table)
    clause, params = _where_clause(where)
    return conn.execute(f"SELECT COUNT(*) FROM {table}{clause}", params).fetchone()[0]
