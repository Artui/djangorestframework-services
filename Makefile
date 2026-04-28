.PHONY: help init test lint lint-fix format format-check type-check deps-bump docs-serve docs-build

help:
	@echo "Available targets:"
	@echo "  init          Sync deps (all groups) and install pre-commit hooks"
	@echo "  test          Run pytest with coverage (100% required)"
	@echo "  lint          Run ruff check + ty check"
	@echo "  lint-fix      Auto-fix lint issues with ruff"
	@echo "  format        Format with ruff"
	@echo "  format-check  Verify formatting"
	@echo "  type-check    Run ty over the package"
	@echo "  deps-bump     Upgrade pinned dependencies"
	@echo "  docs-serve    Live-reload docs at http://localhost:8000 (needs mkdocs.yml)"
	@echo "  docs-build    Build docs into ./site (strict — fails on broken links)"

init:
	uv sync --all-groups
	uv run pre-commit install

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ty check rest_framework_services

lint-fix:
	uv run ruff check --fix .

format:
	uv run ruff format .

format-check:
	uv run ruff format --check --diff .

type-check:
	uv run ty check rest_framework_services

deps-bump:
	uvx uv-upx upgrade run --profile with_pinned

docs-serve:
	uv run --group docs mkdocs serve

docs-build:
	uv run --group docs mkdocs build --strict
