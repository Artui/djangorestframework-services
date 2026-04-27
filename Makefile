.PHONY: lint lint-fix format format-check type-check deps-bump test

lint:
	uv run ruff check
	uv run ty check rest_framework_services

lint-fix:
	uv run ruff check --fix

format:
	uv run ruff format

format-check:
	uv run ruff format --check

type-check:
	uv run ty check rest_framework_services

deps-bump:
	uvx uv-upx upgrade run --profile with_pinned

test:
	uv run pytest
