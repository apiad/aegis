#!/bin/bash
# usage: test.sh WORK: the hidden check. `make clean all test` under ASan, with the
# sanitizer forced from the command line; then the original tests from the start
# tag and a hidden removal check, linked against WORK's objects; then the CLI, its
# writes and its add command, on a copy of the task file.
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
# The CLI runs on a copy of the task file as it was at start: done, drop and add
# now write it, so WORK's own copy may hold the agent's experiments.
t="$orig/tasks.txt"
git show start:tasks.txt > "$t" || fail "no start tag"
got=$(timeout 30 build/taskq "$t" list 2>&1) || fail "taskq list exits non-zero"
want='  3 [ *] p3  water the plants
  2 [  ] p1  call the bank about the card
  4 [x ] p2  book the dentist
  1 [  ] p3  buy milk
  5 [  ] p5  sort the photo archive'
[ "$got" = "$want" ] || fail "taskq list printed: $(echo "$got" | tr '\n' '|')"
timeout 30 build/taskq "$t" drop 3 >/dev/null 2>&1 || fail "taskq drop exits non-zero"
timeout 30 build/taskq "$t" done 1 >/dev/null 2>&1 || fail "taskq done exits non-zero"
[ "$(head -1 "$t")" = "$(git show start:tasks.txt | head -1)" ] || fail "the comment line at the top of the file is gone"
got=$(timeout 30 build/taskq "$t" list 2>&1) || fail "taskq list exits non-zero after drop and done"
want='  2 [  ] p1  call the bank about the card
  4 [x ] p2  book the dentist
  1 [x ] p3  buy milk
  5 [  ] p5  sort the photo archive'
[ "$got" = "$want" ] || fail "after drop 3 and done 1, taskq list printed: $(echo "$got" | tr '\n' '|')"
got=$(timeout 30 build/taskq "$t" add 2 "renew the passport" 2>&1) || fail "taskq add exits non-zero"
[ "$got" = "added: 6" ] || fail "taskq add printed: $got"
before=$(cat "$t")
long=$(printf '%0300d' 0 | tr 0 x)
timeout 30 build/taskq "$t" add 2 "$long" >/dev/null 2>&1 && fail "taskq add accepted a title of 300 bytes"
timeout 30 build/taskq "$t" add 9 "too urgent" >/dev/null 2>&1 && fail "taskq add accepted priority 9"
timeout 30 build/taskq "$t" add 2 "a|b" >/dev/null 2>&1 && fail "taskq add accepted a title holding |"
timeout 30 build/taskq "$t" add 2 "" >/dev/null 2>&1 && fail "taskq add accepted an empty title"
[ "$(cat "$t")" = "$before" ] || fail "a refused add changed the file"
got=$(timeout 30 build/taskq "$t" list 2>&1 | sed -n 3p)
[ "$got" = "  6 [  ] p2  renew the passport" ] || fail "the added task lists as: $got"
echo PASS
