#!/bin/bash
# usage: test.sh WORK: the hidden check. Reads main as committed, not the working
# tree: no merge in progress, no conflict markers, the four subjects in its log,
# and its own tests plus the hidden ones (every feature, in the Line API) green.
set -uo pipefail
fail() { echo "FAIL: $1"; exit 1; }
here=$(cd "$(dirname "$0")" && pwd)
cd "$1" || fail "no WORK folder"
git rev-parse -q --verify MERGE_HEAD >/dev/null && fail "a merge is still in progress"
[ -z "$(git ls-files -u)" ] || fail "unmerged paths remain"
git grep -qE '^(<<<<<<<|>>>>>>>)' main -- . && fail "conflict markers committed on main"
subjects=$(git log main --format=%s) || fail "no main branch"
for subject in "feat: round money to cents" "fix: round each line to cents" \
  "refactor: invoice lines are Line objects" "feat: discount codes" "feat: bulk discount needs ten items"; do
  grep -qxF "$subject" <<<"$subjects" || fail "main's log lacks \"$subject\""
done
tree=$(mktemp -d)
git archive main | tar -x -C "$tree" || fail "no main branch"
mkdir "$tree/hidden" && cp "$here"/check_*.py "$tree/hidden/"
cd "$tree" || fail "no archive"
timeout 60 python3 -m pytest -q -p no:cacheprovider tests >/dev/null || fail "main's own tests do not pass"
timeout 60 python3 -m pytest -q -p no:cacheprovider hidden/check_*.py >/dev/null || fail "the hidden feature tests do not pass on main"
echo PASS
