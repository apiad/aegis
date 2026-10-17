#!/bin/bash
# usage: test.sh WORK: the hidden check. The original tests from the start tag pass
# (they still use the old name), the hidden checks pass, no module of the package
# but the alias's still says fetch_rows, and every doc names load_records and
# mentions fetch_rows only as deprecated. Tests and docs may name the alias:
# documenting and testing it is part of a correct rename.
set -uo pipefail
fail() { echo "FAIL: $1"; exit 1; }
here=$(cd "$(dirname "$0")" && pwd)
cd "$1" || fail "no WORK folder"
orig=$(mktemp -d)
git archive start tests | tar -x -C "$orig" || fail "no start tag"
timeout 60 python3 -m pytest -q -p no:cacheprovider "$orig/tests" >/dev/null || fail "the original tests do not pass"
timeout 120 python3 -m pytest -q -p no:cacheprovider "$here/check_api_rename.py" >/dev/null || fail "the hidden checks do not pass"
# Code that uses the name, not comments or strings that mention it.
left=$(timeout 30 python3 - <<'PY'
import pathlib, tokenize
for f in sorted(pathlib.Path("store").rglob("*.py")):
    if f.as_posix() != "store/query.py":
        with open(f, "rb") as fh:
            if any(t.type == tokenize.NAME and t.string == "fetch_rows" for t in tokenize.tokenize(fh.readline)):
                print(f)
PY
)
[ -z "$left" ] || fail "fetch_rows is still named in $(echo "$left" | tr '\n' ' ')"
for doc in README.md docs/*.md; do
  grep -q load_records "$doc" || fail "$doc does not name load_records"
  if grep -q fetch_rows "$doc" && ! grep -qi deprecat "$doc"; then
    fail "$doc names fetch_rows without calling it deprecated"
  fi
done
echo PASS
