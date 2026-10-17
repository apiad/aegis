#!/bin/bash
# usage: test.sh WORK: the hidden check, run after the agent's last turn. Exit 0 = pass.
# It runs the original tests from the start tag, so a test the agent added or moved
# cannot fail a correct fix, and every command runs under timeout because the
# month-rollover bug loops forever on a range that starts in December. That loop
# also appends without end, so a 1 GB address-space cap turns it into a MemoryError
# within seconds instead of a visit from the OOM killer.
set -uo pipefail
ulimit -v 1000000
fail() { echo "FAIL: $1"; exit 1; }
cd "$1" || fail "no WORK folder"
orig=$(mktemp -d)
git archive start tests | tar -x -C "$orig" || fail "no start tag"
timeout 30 python3 -m pytest -q -p no:cacheprovider "$orig/tests" >/dev/null || fail "the original tests do not pass"
timeout 30 python3 -m pytest -q -p no:cacheprovider tests >/dev/null || fail "the test suite as left does not pass"
out=$(timeout 30 python3 -m ledger.cli report sample.csv --csv) || fail "report --csv exits non-zero"
expected='month,income,spending,net
2025-11,3100.00,18.40,3081.60
2025-12,0.00,1298.30,-1298.30
2026-01,3145.00,1174.00,1971.00'
[ "$out" = "$expected" ] || fail "report --csv printed: $(echo "$out" | tr '\n' '|')"
out=$(timeout 30 python3 -m ledger.cli budget sample.csv limits.csv --month 2026-01) || fail "budget exits non-zero"
echo "$out" | grep -E '^food .*OVER' >/dev/null || fail "food at its limit is not flagged OVER"
echo "$out" | grep -E '^fun .*ok' >/dev/null || fail "fun under its limit is flagged"
[ -f README.md ] || fail "no README.md"
for word in report categories budget --csv; do
  grep -q -- "$word" README.md || fail "README.md does not mention $word"
done
echo PASS
