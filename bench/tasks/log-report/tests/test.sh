#!/bin/bash
# usage: test.sh WORK: the hidden check. Recomputes the report from the logs as they
# were at the start tag, with its own script, and compares report.json exactly.
set -uo pipefail
fail() { echo "FAIL: $1"; exit 1; }
here=$(cd "$(dirname "$0")" && pwd)
cd "$1" || fail "no WORK folder"
orig=$(mktemp -d)
git archive start logs | tar -x -C "$orig" || fail "no start tag"
timeout 120 python3 "$here/check_report.py" "$orig/logs" report.json
