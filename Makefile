.PHONY: check lint format typecheck test coverage

check: format lint typecheck test

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
