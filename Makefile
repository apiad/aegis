.PHONY: check lint lint-docs lint-detail format format-check typecheck changelog changelog-check test test-cov test-all test-slow test-live test-browser bench coverage know-how site-css

# Every gate, on the fast lane: the tests marked slow, the browser tests among
# them, are `test-slow`'s. The tests run before typecheck so a type error cannot
# hide a failing suite; both block.
check: format lint lint-docs changelog-check test typecheck

# Doc drift (.rift.yaml). Not in CI: rift is private and not on PyPI, so a
# runner cannot install it. `make lint-detail` names each entity.
lint-docs:
	rift check

lint-detail:
	rift list

lint:
	uv run ruff check --fix src/ tests/ scripts/

format:
	uv run ruff format src/ tests/ scripts/

# What CI runs. `format` rewrites; a runner has to fail instead, or drift lands
# green and the next person's tree is dirty for reasons they did not cause.
format-check:
	uv run ruff format --check src/ tests/ scripts/

# Every fragment in changelog.d/ parses. A fragment that does not parse is a
# release note that silently goes missing.
changelog-check:
	uv run python scripts/changelog.py check

# The collated CHANGELOG, to stdout. Writes nothing; `apply` is the releaser's
# verb and lives in know-how/releasing.md.
changelog:
	@uv run python scripts/changelog.py preview

# Blocking. The legacy tree reported ~235 diagnostics and kept this advisory;
# this tree has none, and new code keeps it that way.
typecheck:
	uv run ty check src/

# The fast lane: hermetic, slow tests deselected, spread across cores. A test
# over the budget without @pytest.mark.slow fails it (tests/conftest.py).
# A red run is a regression to investigate, not noise to re-roll.
test:
	uv run pytest -q -n auto -m "not slow" --max-unmarked-duration=3

test-cov:
	uv run pytest -q -n auto -m "not slow" --cov=aegis --cov-report=term-missing

# Everything hermetic, slow tests included.
test-all:
	uv run pytest -q -n auto --cov=aegis --cov-report=term-missing

# Every test the fast lane skips, serially, as CI runs them: the browser tests,
# restarts, detached servers. With `check` it is what CI runs; AGENTS.md's done
# line names both (tests/test_lanes.py holds them together).
test-slow:
	uv run pytest -q -m "slow and not live"

# The client in headless Chromium against a real `aegis serve` and the fake
# claude. Marked slow too, so `make test` skips them; CI runs them.
test-browser:
	uv run pytest -q -m browser

# The landing page's copy of the client's CSS (tests/test_site.py checks it).
site-css:
	uv run python scripts/site_css.py

# The transcript path, measured (scripts/bench.py). Reports, never gates.
bench:
	uv run python scripts/bench.py --out .aegis-bench.json

# Real agent CLIs and models: spends quota. Select by marker, never with
# -k "not live": -k matches substrings and silently drops unrelated tests whose
# names contain "live", such as anything with "deliver".
test-live:
	uv run pytest -q --run-live -m live

coverage:
	uv run pytest -n auto --cov=aegis --cov-report=html

# The know-how menu: one when: line per procedure doc.
know-how:
	@grep -m1 -H '^when:' know-how/*.md
