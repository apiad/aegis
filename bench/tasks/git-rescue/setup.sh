#!/bin/bash
# usage: setup.sh WORK: replay the history below into WORK and leave it broken.
#
#   main:  base, A (money rounding), fix (round each line)   <- tagged start
#   refactor-lines: from A, invoice lines become Line objects
#   discounts: from A, B1 (discount codes), B2 (bulk discount); then a rebase with
#     the wrong upstream drops both and the branch is deleted, so B1 and B2 are
#     reachable only from the reflog
#   finally a merge of refactor-lines into main, stopped with the test file
#   resolved and staged and invoice.py still holding conflict markers
set -euo pipefail
stages="$(cd "$(dirname "$0")" && pwd)/stages"
cd "$1"
export GIT_AUTHOR_NAME=dev GIT_AUTHOR_EMAIL=dev@example.com
export GIT_COMMITTER_NAME=dev GIT_COMMITTER_EMAIL=dev@example.com
n=0
commit() {  # commit STAGE MESSAGE: lay STAGE's files over the tree and commit them
  n=$((n + 1))
  export GIT_AUTHOR_DATE="2026-09-0${n}T10:00:00Z" GIT_COMMITTER_DATE="2026-09-0${n}T10:00:00Z"
  cp -r "$stages/$1/." .
  git add -A
  git commit -qm "$2"
}
git init -q -b main
git config merge.conflictstyle merge
printf '__pycache__/\n.pytest_cache/\n' > .gitignore
commit base "feat: invoice totals"
commit a "feat: round money to cents"
git checkout -qb refactor-lines
commit refactor "refactor: invoice lines are Line objects"
git checkout -q main
git checkout -qb discounts
commit b1 "feat: discount codes"
commit b2 "feat: bulk discount needs ten items"
git checkout -q main
commit fix "fix: round each line to cents"
git tag start
git checkout -q discounts
git rebase -q --onto main HEAD discounts
git checkout -q main
git branch -q -D discounts
git merge -q --no-edit refactor-lines >/dev/null 2>&1 && { echo "expected a conflict" >&2; exit 1; }
cp "$stages/half/tests/test_invoice.py" tests/test_invoice.py
git add tests/test_invoice.py
