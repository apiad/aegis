#!/bin/bash
# usage: test.sh WORK: the hidden check. `make clean all test` under ASan, with the
# sanitizer forced from the command line; then the original tests from the start
# tag and a hidden removal check, linked against WORK's objects; then the CLI.
set -uo pipefail
fail() { echo "FAIL: $1"; exit 1; }
here=$(cd "$(dirname "$0")" && pwd)
command -v cc >/dev/null || fail "this task needs a C compiler (cc)"
command -v make >/dev/null || fail "this task needs make"
cd "$1" || fail "no WORK folder"
export ASAN_OPTIONS=detect_leaks=0
asan="-std=c11 -g -O1 -fsanitize=address -fno-omit-frame-pointer"
timeout 300 make clean all test CFLAGS="$asan" LDFLAGS=-fsanitize=address >/dev/null 2>&1 || fail "make clean all test under ASan does not pass"
orig=$(mktemp -d)
git archive start tests | tar -x -C "$orig" || fail "no start tag"
cp "$here/check_remove.c" "$orig/tests/"
for t in test_list test_parse check_remove; do
  timeout 60 cc -Iinclude -I"$orig/tests" -D_POSIX_C_SOURCE=200809L $asan "$orig/tests/$t.c" build/item.o build/list.o build/parse.o -fsanitize=address -o "$orig/$t" 2>/dev/null || fail "$t does not build against WORK's objects"
  timeout 60 "$orig/$t" >/dev/null 2>&1 || fail "$t fails under ASan"
done
got=$(timeout 30 build/taskq tasks.txt list 2>&1) || fail "taskq list exits non-zero"
want='  3 [ *] p3  water the plants
  2 [  ] p1  call the bank about the card
  4 [x ] p2  book the dentist
  1 [  ] p3  buy milk
  5 [  ] p5  sort the photo archive'
[ "$got" = "$want" ] || fail "taskq list printed: $(echo "$got" | tr '\n' '|')"
timeout 30 build/taskq tasks.txt drop 3 >/dev/null 2>&1 || fail "taskq drop exits non-zero"
echo PASS
