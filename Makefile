.PHONY: check lint lint-docs lint-detail format typecheck test coverage

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

test:
	uv run pytest --cov=aegis --cov-report=term-missing

coverage:
	uv run pytest --cov=aegis --cov-report=html
