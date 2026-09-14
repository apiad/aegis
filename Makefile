.PHONY: check lint lint-docs lint-detail format typecheck test test-cov test-all test-live coverage

check: format lint lint-docs typecheck test

# Doc drift (.rift.yaml). Not in CI — rift is private and not on PyPI, so a
# runner cannot install it. See the "Doc lint" section of AGENTS.md.
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
test:
	uv run pytest -q -n auto -m "not slow" --max-unmarked-duration=3

test-cov:
	uv run pytest -q -n auto -m "not slow" --cov=aegis --cov-report=term-missing

# Everything hermetic, slow tests included.
test-all:
	uv run pytest -q -n auto --cov=aegis --cov-report=term-missing

# Real agent CLIs, models and remote hosts: spends quota, touches the VPS.
test-live:
	uv run pytest -q --run-live -m live

coverage:
	uv run pytest -n auto --cov=aegis --cov-report=html
