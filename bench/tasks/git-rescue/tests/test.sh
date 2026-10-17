#!/bin/bash
# usage: test.sh WORK: the hidden check. Reads main as committed, not the working
# tree: no merge in progress, no conflict markers, the subjects in its log, the
# discounts branch and the v1.2.0 tag where the follow-ups put them, and its own
# tests plus the hidden ones (every feature, in the Line API, twelve for BULK5) green.
set -uo pipefail
fail() { echo "FAIL: $1"; exit 1; }
here=$(cd "$(dirname "$0")" && pwd)
cd "$1" || fail "no WORK folder"
git rev-parse -q --verify MERGE_HEAD >/dev/null && fail "a merge is still in progress"
[ -z "$(git ls-files -u)" ] || fail "unmerged paths remain"
git grep -qE '^(<<<<<<<|>>>>>>>)' main -- . && fail "conflict markers committed on main"
subjects=$(git log main --format=%s) || fail "no main branch"
for subject in "feat: round money to cents" "fix: round each line to cents" \
  "refactor: invoice lines are Line objects" "feat: discount codes" "feat: bulk discount needs ten items" \
  "fix: bulk discount needs twelve items"; do
  grep -qxF "$subject" <<<"$subjects" || fail "main's log lacks \"$subject\""
done
[ "$(git log -1 --format=%s discounts 2>/dev/null)" = "feat: bulk discount needs ten items" ] || fail "the discounts branch is not at the bulk-discount commit"
git merge-base --is-ancestor discounts main 2>/dev/null || fail "the discounts branch is not on main"
[ "$(git cat-file -t v1.2.0 2>/dev/null)" = tag ] || fail "v1.2.0 is not an annotated tag"
[ "$(git rev-parse 'v1.2.0^{commit}')" = "$(git rev-parse main)" ] || fail "v1.2.0 is not at main"
[ "$(git log -1 --format=%s main)" = "fix: bulk discount needs twelve items" ] || fail "main does not end on the twelve-items fix"
[ "$(git tag -l --format='%(contents:subject)' v1.2.0)" = "tally 1.2.0" ] || fail "v1.2.0's message is not \"tally 1.2.0\""
tree=$(mktemp -d)
git archive main | tar -x -C "$tree" || fail "no main branch"
mkdir "$tree/hidden" && cp "$here"/check_*.py "$tree/hidden/"
cd "$tree" || fail "no archive"
timeout 60 python3 -m pytest -q -p no:cacheprovider tests >/dev/null || fail "main's own tests do not pass"
timeout 60 python3 -m pytest -q -p no:cacheprovider hidden/check_*.py >/dev/null || fail "the hidden feature tests do not pass on main"
echo PASS
