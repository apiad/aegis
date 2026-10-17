#!/bin/bash
# usage: setup.sh WORK: copy the starting state into WORK and commit it, tagged start.
set -euo pipefail
cp -r "$(dirname "$0")/files/." "$1"/
cd "$1"
git init -q -b main
git add -A
git -c user.name=bench -c user.email=bench@example.com commit -qm "start"
git tag start
