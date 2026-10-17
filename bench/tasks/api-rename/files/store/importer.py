import csv
from pathlib import Path

from .db import TABLES
from .query import count_rows


def load_csv(conn, table: str, path: Path) -> int:
    """Insert every row of a CSV whose header names `table`'s columns."""
    if table not in TABLES:
        raise ValueError(f"unknown table {table!r}")
    before = count_rows(conn, table)
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    if rows:
        cols = list(rows[0])
        marks = ", ".join("?" for _ in cols)
        conn.executemany(
            f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({marks})", [[r[c] or None for c in cols] for r in rows]
        )
        conn.commit()
    return count_rows(conn, table) - before
