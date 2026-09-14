.PHONY: check lint lint-docs lint-detail format typecheck test test-cov test-all test-live coverage know-how

check: format lint lint-docs typecheck test

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

typecheck:
	uv run ty check src/

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
