.PHONY: check lint format typecheck

check: format lint typecheck

lint:
	uv run ruff check --fix src/

format:
	uv run ruff format src/

typecheck:
	uv run ty check src/