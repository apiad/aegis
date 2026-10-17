#!/bin/bash
# usage: test.sh WORK: the hidden check. A faster suite than the visible one: a
# server without the warm-up, failing in scripted patterns, timing each backoff.
set -uo pipefail
fail() { echo "FAIL: $1"; exit 1; }
here=$(cd "$(dirname "$0")" && pwd)
cd "$1" || fail "no WORK folder"
timeout 120 python3 -m pytest -q -p no:cacheprovider "$here/check_retry.py" >/dev/null || fail "the hidden retry checks do not pass"
echo PASS
