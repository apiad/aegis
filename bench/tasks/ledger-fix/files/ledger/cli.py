import argparse
import sys
from pathlib import Path

from .budget import check, is_over, read_limits
from .parse import parse_date, read_csv
from .report import by_category, monthly


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ledger")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report", help="monthly income, spending and net")
    r.add_argument("csv", type=Path)
    r.add_argument("--account", default="main")
    c = sub.add_parser("categories", help="spending by category")
    c.add_argument("csv", type=Path)
    c.add_argument("--account", default="main")
    c.add_argument("--since", type=parse_date, help="only count days on or after this date")
    b = sub.add_parser("budget", help="one month's spending against each category's limit")
    b.add_argument("csv", type=Path)
    b.add_argument("limits", type=Path, help="CSV with columns category,limit")
    b.add_argument("--month", required=True, help="YYYY-MM")
    b.add_argument("--account", default="main")
    a = ap.parse_args(argv)
    txs = read_csv(a.csv, a.account)
    if a.cmd == "report":
        for row in monthly(txs):
            print(f"{row['month']}  +{row['income']:>10}  -{row['spending']:>10}  ={row['net']:>10}")
    elif a.cmd == "categories":
        for cat, amt in by_category(txs, a.since).items():
            print(f"{cat:<20}{amt:>10}")
    else:
        for line in check(txs, read_limits(a.limits), a.month):
            flag = "OVER" if is_over(line) else "ok"
            print(f"{line.category:<20}{line.spent:>10} / {line.limit:<10}{flag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
