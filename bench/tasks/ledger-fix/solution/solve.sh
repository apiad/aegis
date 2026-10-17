#!/bin/bash
# Oracle: fix the three bugs, add report --csv with a test in a new file, write the README.
set -euo pipefail
cd "$1"
python3 - <<'PY'
import pathlib

def edit(path, old, new):
    p = pathlib.Path(path)
    s = p.read_text()
    assert old in s, (path, old)
    p.write_text(s.replace(old, new, 1))

edit("ledger/parse.py", "        text = text[1:-1]\n    return Decimal(text)", "        return -Decimal(text[1:-1])\n    return Decimal(text)")
edit("ledger/report.py", "if m == 12:", "if m == 13:")
edit("ledger/budget.py", "return line.spent > line.limit", "return line.spent >= line.limit")
# argparse would store --csv in a.csv over the positional of the same name: give it its own dest.
edit("ledger/cli.py", 'r.add_argument("--account", default="main")',
     'r.add_argument("--account", default="main")\n    r.add_argument("--csv", dest="as_csv", action="store_true", help="print CSV")')
edit("ledger/cli.py", '    if a.cmd == "report":\n', '''    if a.cmd == "report" and a.as_csv:
        print("month,income,spending,net")
        for row in monthly(txs):
            print(f"{row['month']},{row['income']:.2f},{row['spending']:.2f},{row['net']:.2f}")
    elif a.cmd == "report":
''')
pathlib.Path("tests/test_cli.py").write_text('''from ledger.cli import main


def test_report_csv(capsys):
    main(["report", "sample.csv", "--csv"])
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "month,income,spending,net"
    assert lines[1] == "2025-11,3100.00,18.40,3081.60"
''')
pathlib.Path("README.md").write_text("""# ledger

- `python3 -m ledger.cli report sample.csv` prints income, spending and net per month.
  Add `--csv` for CSV with the header `month,income,spending,net`.
- `python3 -m ledger.cli categories sample.csv --since 2026-01-01` prints spending by category.
- `python3 -m ledger.cli budget sample.csv limits.csv --month 2026-01` flags categories at or over their limit.
""")
PY
