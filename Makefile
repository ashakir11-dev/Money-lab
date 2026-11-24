.PHONY: install check lint format typecheck test acceptance dashboard demo fixture

install:
	uv sync

check: lint typecheck test

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .
	uv run ruff check --fix .

typecheck:
	uv run mypy

test:
	uv run pytest

acceptance:
	uv run moneylab acceptance

dashboard:
	uv run moneylab dashboard

demo:
	uv run moneylab demo

fixture:
	uv run python scripts/make_fixture.py
