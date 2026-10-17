#!/bin/bash
# usage: setup.sh WORK: generate the rotated logs into WORK/logs and commit them, tagged start.
set -euo pipefail
python3 "$(dirname "$0")/gen_logs.py" "$1/logs"
cd "$1"
git init -q -b main
git add -A
git -c user.name=bench -c user.email=bench@example.com commit -qm "start"
git tag start
