#!/bin/bash
# usage: test.sh WORK: the hidden check. The original tests from the start tag pass
# (they still use the old name), the hidden checks pass, and no file outside the
# alias's module and the changelog still says fetch_rows.
set -uo pipefail
fail() { echo "FAIL: $1"; exit 1; }
here=$(cd "$(dirname "$0")" && pwd)
cd "$1" || fail "no WORK folder"
orig=$(mktemp -d)
git archive start tests | tar -x -C "$orig" || fail "no start tag"
timeout 60 python3 -m pytest -q -p no:cacheprovider "$orig/tests" >/dev/null || fail "the original tests do not pass"
timeout 120 python3 -m pytest -q -p no:cacheprovider "$here/check_api_rename.py" >/dev/null || fail "the hidden checks do not pass"
left=$(grep -rnI --exclude-dir=.git --exclude-dir=__pycache__ --exclude-dir=.pytest_cache \
  --exclude=check_api_rename.py --exclude=test.sh fetch_rows . | grep -v -e '^\./store/query\.py:' -e '^\./CHANGELOG\.md:')
[ -z "$left" ] || fail "fetch_rows is still named in $(echo "$left" | cut -d: -f1 | sort -u | tr '\n' ' ')"
echo PASS
