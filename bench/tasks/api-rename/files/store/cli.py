import argparse
import sys
from datetime import date

from .books import list_books
from .cache import cached
from .db import connect
from .export import to_csv
from .loans import overdue
from .reports import popular_books
from .search import search_books
from .stats import summary
from .validate import problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="store", description="A small library catalogue.")
    ap.add_argument("db", help="SQLite file")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("books", help="every book, by title").add_argument("--author")
    sub.add_parser("search", help="books by title or author").add_argument("term")
    sub.add_parser("overdue", help="loans past due today")
    sub.add_parser("popular", help="the most borrowed titles").add_argument("--top", type=int, default=5)
    sub.add_parser("stats", help="counts per table")
    sub.add_parser("check", help="integrity problems")
    sub.add_parser("export", help="a table as CSV").add_argument("table")
    sub.add_parser("rows", help="raw rows of a table via fetch_rows").add_argument("table")
    a = ap.parse_args(argv)
    conn = connect(a.db)
    if a.cmd == "books":
        for b in list_books(conn, a.author):
            print(f"{b.id:>4}  {b.title}  ({b.author}, {b.year})")
    elif a.cmd == "search":
        for b in search_books(conn, a.term):
            print(f"{b.id:>4}  {b.title}  ({b.author})")
    elif a.cmd == "overdue":
        for loan in overdue(conn, date.today()):
            print(f"loan {loan.id}: book {loan.book_id}, member {loan.member_id}, due {loan.due}")
    elif a.cmd == "popular":
        for title, n in popular_books(conn, a.top):
            print(f"{n:>4}  {title}")
    elif a.cmd == "stats":
        for k, v in summary(conn).items():
            print(f"{k:<10}{v:>6}")
    elif a.cmd == "check":
        found = problems(conn)
        print("\n".join(found) or "no problems")
        return 1 if found else 0
    elif a.cmd == "export":
        to_csv(conn, a.table)
    else:
        reader = cached(conn)
        for row in reader.fetch_rows(a.table, order_by="id"):
            print(row)
    return 0


if __name__ == "__main__":
    sys.exit(main())
