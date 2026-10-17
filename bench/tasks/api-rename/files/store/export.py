import csv
import sys

from .query import fetch_rows


def to_csv(conn, table: str, out=None, order_by: str = "id") -> int:
    """Write `table` as CSV with a header row; returns the number of data rows."""
    out = out or sys.stdout
    rows = fetch_rows(conn, table, order_by=order_by)
    if not rows:
        return 0
    w = csv.DictWriter(out, fieldnames=list(rows[0]), lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return len(rows)
