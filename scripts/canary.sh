#!/usr/bin/env bash
# The canary: the latest stable release of every harness under a real aegis,
# against a fake model (tests/canary/). Installs the releases into a folder of
# its own, never over the ones on PATH, and passes its arguments to pytest:
#
#   scripts/canary.sh                       # every scenario, every setup
#   scripts/canary.sh -k "read_session and claude"
#   scripts/canary.sh 'tests/canary/test_harnesses.py::test_x[codex]'
#
# AEGIS_CANARY_HOME   where the releases go (default ~/.cache/aegis-canary)
# AEGIS_CANARY_REPORT where a failing test writes its report (default .canary)
# AEGIS_CANARY_NO_INSTALL=1 reuses what is installed there
set -euo pipefail

home="${AEGIS_CANARY_HOME:-$HOME/.cache/aegis-canary}"
cd "$(dirname "$0")/.."

# OpenCode 2 is @opencode/cli; opencode-ai is OpenCode 1, what opencode.ai/install
# installs, kept apart because both install a binary named opencode.
if [[ "${AEGIS_CANARY_NO_INSTALL:-}" != 1 ]]; then
  mkdir -p "$home/npm" "$home/oc1"
  npm install --prefix "$home/npm" --no-fund --no-audit --silent \
    @anthropic-ai/claude-code@latest @openai/codex@latest @opencode/cli@latest
  npm install --prefix "$home/oc1" --no-fund --no-audit --silent opencode-ai@latest
fi

export AEGIS_CANARY_BIN="$home/npm/node_modules/.bin"
export AEGIS_CANARY_OPENCODE1="$home/oc1/node_modules/.bin"
export AEGIS_CANARY_REPORT="${AEGIS_CANARY_REPORT:-.canary}"
rm -rf "$AEGIS_CANARY_REPORT"
mkdir -p "$AEGIS_CANARY_REPORT"

{
  echo "claude-code $("$AEGIS_CANARY_BIN/claude" --version)"
  echo "codex $("$AEGIS_CANARY_BIN/codex" --version)"
  echo "opencode $("$AEGIS_CANARY_BIN/opencode" --version)"
  echo "opencode-1 $("$AEGIS_CANARY_OPENCODE1/opencode" --version)"
} | tee "$AEGIS_CANARY_REPORT/versions.txt"

# A test named on the command line runs alone; otherwise the whole folder.
where=(tests/canary)
for a in "$@"; do [[ "$a" == tests/* ]] && where=(); done

run() {
  uv run pytest -q --run-canary "${where[@]}" -p no:randomly \
    -n "${AEGIS_CANARY_WORKERS:-4}" -W ignore::DeprecationWarning "$@"
}

# A failure is run once more, alone, before anyone hears of it: a harness that
# was slow to start once on a loaded runner is not a break. The first pass's
# reports move to first-pass/; the issues come from the second.
rc=0
run --junitxml "$AEGIS_CANARY_REPORT/junit-1.xml" "$@" || rc=$?
if [[ $rc == 1 ]]; then
  mkdir -p "$AEGIS_CANARY_REPORT/first-pass"
  mv "$AEGIS_CANARY_REPORT"/*.json "$AEGIS_CANARY_REPORT/first-pass/" 2>/dev/null || true
  rc=0
  AEGIS_CANARY_WORKERS=1 run --lf --junitxml "$AEGIS_CANARY_REPORT/junit-2.xml" "$@" || rc=$?
fi
exit $rc
