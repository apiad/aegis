.PHONY: check lint lint-docs lint-detail format format-check typecheck typecheck-advisory changelog changelog-check test test-cov test-all test-live test-browser bench2 coverage know-how

# The order is the point: the tests run before the advisory stage, so nothing
# optional can stop the gate from reaching them. `typecheck` used to sit here
# and abort `make check` every single time, which meant the gate had never once
# run the suite it exists to run.
check: format lint lint-docs changelog-check test typecheck-advisory

# Doc drift (.rift.yaml). Not in CI: rift is private and not on PyPI, so a
# runner cannot install it. A failure exits 2, not 1, because make adds its own
# code on top of rift's; gate on non-zero. `make lint-detail` names each entity.
lint-docs:
	rift check

lint-detail:
	rift list

lint:
	uv run ruff check --fix src/

format:
	uv run ruff format src/

# What CI runs. `format` rewrites; a runner has to fail instead, or drift lands
# green and the next person's tree is dirty for reasons they did not cause.
format-check:
	uv run ruff format --check src/

# Every fragment in changelog.d/ parses. Blocking, unlike typecheck: it is a
# filename check over a handful of files, it has never had a backlog, and a
# fragment that does not parse is a release note that silently goes missing.
changelog-check:
	uv run python -m aegis.changelog check

# The collated CHANGELOG, to stdout. Writes nothing; `apply` is the releaser's
# verb and lives in know-how/releasing.md.
changelog:
	@uv run python -m aegis.changelog preview

# Strict. This is the gate we want, and the one to run while driving the number
# down — see issue #8.
typecheck:
	uv run ty check src/

# Advisory until that list is empty. `ty` is 0.0.29 and has never been
# configured here, so its default ruleset reports ~235 diagnostics on code that
# works; a stage that always fails gates nothing and hid the four stages that
# do. Same rule this repo already follows for `rift`: keep it at warning,
# promote it to blocking once the list is empty for reasons we agree with.
# Then this target and `check`'s dependency on it both go away.
typecheck-advisory:
	@uv run ty check src/ || \
	  echo "^ typecheck is ADVISORY (issue #8) — not failing the gate"

# The fast lane: hermetic, slow tests deselected, spread across cores. A test
# over the budget without @pytest.mark.slow fails it (tests/conftest.py).
# A red run is a regression to investigate, not noise to re-roll: the old flakes
# (inotify leaks, teardown races, shared .aegis/state) are fixed, and re-running
# until green now hides real failures.
test:
	uv run pytest -q -n auto -m "not slow" --max-unmarked-duration=3

test-cov:
	uv run pytest -q -n auto -m "not slow" --cov=aegis --cov-report=term-missing

# Everything hermetic, slow tests included.
test-all:
	uv run pytest -q -n auto --cov=aegis --cov-report=term-missing

# aegis2's client in headless Chromium against a real `aegis2 serve` and the
# fake claude. Marked slow too, so `make test` skips them; CI runs them.
test-browser:
	uv run pytest -q -m browser

# aegis2's transcript path, measured (scripts/bench2.py). Reports, never gates.
bench2:
	uv run python scripts/bench2.py --out .aegis2-bench.json

# Real agent CLIs, models and remote hosts: spends quota, touches the VPS.
# Select by marker, never with -k "not live": -k matches substrings and silently
# drops unrelated tests whose names contain "live", such as anything with "deliver".
test-live:
	uv run pytest -q --run-live -m live

coverage:
	uv run pytest -n auto --cov=aegis --cov-report=html

# The know-how menu: one when: line per procedure doc.
know-how:
	@grep -m1 -H '^when:' know-how/*.md
