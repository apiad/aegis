#!/bin/bash
# usage: test.sh WORK: the hidden check. Recomputes the report from the logs as they
# were at the start tag, with its own script, and compares report.json exactly;
# then runs WORK's make_report.py on another server's logs and compares that too.
set -uo pipefail
fail() { echo "FAIL: $1"; exit 1; }
here=$(cd "$(dirname "$0")" && pwd)
cd "$1" || fail "no WORK folder"
orig=$(mktemp -d)
git archive start logs | tar -x -C "$orig" || fail "no start tag"
timeout 120 python3 "$here/check_report.py" "$orig/logs" report.json || exit 1
[ -f make_report.py ] || fail "no make_report.py"
timeout 60 python3 "$here/../gen_logs.py" "$orig/other" other || fail "cannot generate the other server's logs"
timeout 120 python3 make_report.py "$orig/other" "$orig/other.json" >/dev/null 2>&1 || fail "make_report.py fails on another server's logs"
timeout 120 python3 "$here/check_report.py" "$orig/other" "$orig/other.json" "the other server's report"
