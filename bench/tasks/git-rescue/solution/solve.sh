#!/bin/bash
# Oracle: finish the merge, then replay both discount commits from the reflog with
# their messages, adapting them to the Line API; then the follow-ups: the branch
# and the tag, and the twelve-items fix with the tag moved onto it.
set -euo pipefail
cd "$1"
export GIT_AUTHOR_NAME=dev GIT_AUTHOR_EMAIL=dev@example.com GIT_COMMITTER_NAME=dev GIT_COMMITTER_EMAIL=dev@example.com
git show refactor-lines:tally/invoice.py | sed 's/^    return line.qty \* line.unit$/    return to_cents(line.qty * line.unit)/' > tally/invoice.py
git add tally/invoice.py
git commit -q --no-edit
lost() { git log -g -1 --format=%H -F --grep="$1" HEAD; }
b1=$(lost "feat: discount codes")
b2=$(lost "feat: bulk discount needs ten items")
replay() {  # replay SHA: apply it without committing, adapt it to Line, commit with its message
  git cherry-pick -n "$1" >/dev/null 2>&1 || true
  git show "$1:tally/discounts.py" | sed 's/sum(qty for qty, _ in lines)/sum(line.qty for line in lines)/' > tally/discounts.py
  git show "$1:tests/test_discounts.py" > tests/test_discounts.py
  python3 - "$1" <<'PY'
import pathlib, re, subprocess, sys
sha = sys.argv[1]
inv = subprocess.run(["git", "show", "HEAD:tally/invoice.py"], capture_output=True, text=True, check=True).stdout
old = subprocess.run(["git", "show", f"{sha}:tally/invoice.py"], capture_output=True, text=True, check=True).stdout
imp = re.search(r"^from \.discounts import .*$", old, re.M).group(0)
body = re.search(r"    if code:\n(?:        .*\n)+", old).group(0)
inv = re.sub(r"^from \.discounts import .*\n", "", inv, flags=re.M)
inv = inv.replace("from .money import to_cents\n", imp + "\nfrom .money import to_cents\n")
inv = re.sub(r"    if code:\n(?:        .*\n)+", "", inv)
inv = re.sub(r'def invoice_total\(.*\) -> Decimal:\n    s = subtotal\(lines\)\n',
             'def invoice_total(lines: list[Line], rate: Decimal = Decimal("0"), code: str | None = None) -> Decimal:\n    s = subtotal(lines)\n' + body, inv)
pathlib.Path("tally/invoice.py").write_text(inv)
t = pathlib.Path("tests/test_discounts.py")
s = t.read_text().replace("from tally.invoice import invoice_total", "from tally.invoice import Line, invoice_total")
s = re.sub(r"\((\d+), (D\(\"[0-9.]+\"\))\)", r'Line("item", \1, \2)', s)
t.write_text(s)
PY
  git add tally tests
  git commit -q -C "$1"
}
replay "$b1"
replay "$b2"
git branch discounts main
git tag -a v1.2.0 -m "tally 1.2.0"
sed -i 's/MIN_ITEMS = {"BULK5": 10}/MIN_ITEMS = {"BULK5": 12}/' tally/discounts.py
python3 - <<'PY'
import pathlib
t = pathlib.Path("tests/test_discounts.py")
s = t.read_text()
s = s.replace("def test_bulk_needs_ten_items", "def test_bulk_needs_twelve_items")
s = s.replace('Line("item", 10, D("1.00"))], code="BULK5") == D("9.50")', 'Line("item", 12, D("1.00"))], code="BULK5") == D("11.40")')
s = s.replace('Line("item", 9, D("1.00"))], code="BULK5")', 'Line("item", 11, D("1.00"))], code="BULK5")')
t.write_text(s)
PY
git commit -qam "fix: bulk discount needs twelve items"
git tag -fa v1.2.0 -m "tally 1.2.0" >/dev/null
