#!/bin/bash
# Oracle, second way: the follow-up's test goes into the existing test_report.py, not a new file.
set -euo pipefail
bash "$(dirname "$0")/solve.sh" "$1"
cd "$1"
cat tests/test_cli.py >> tests/test_report.py
rm tests/test_cli.py
