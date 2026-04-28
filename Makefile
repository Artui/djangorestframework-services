.PHONY: help init test lint lint-fix format format-check type-check deps-bump docs-serve docs-build release-bump release-publish

help:
	@echo "Available targets:"
	@echo "  init             Sync deps (all groups) and install pre-commit hooks"
	@echo "  test             Run pytest with coverage (100% required)"
	@echo "  lint             Run ruff check + ty check"
	@echo "  lint-fix         Auto-fix lint issues with ruff"
	@echo "  format           Format with ruff"
	@echo "  format-check     Verify formatting"
	@echo "  type-check       Run ty over the package"
	@echo "  deps-bump        Upgrade pinned dependencies"
	@echo "  docs-serve       Live-reload docs at http://localhost:8000 (needs mkdocs.yml)"
	@echo "  docs-build       Build docs into ./site (strict — fails on broken links)"
	@echo "  release-bump     Bump version files + CHANGELOG. Usage: make release-bump VERSION=X.Y.Z"
	@echo "  release-publish  Commit, tag, and push the current version to trigger release.yml"

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

release-bump:
	@if [ -z "$(VERSION)" ]; then \
		echo "Usage: make release-bump VERSION=X.Y.Z"; exit 1; \
	fi
	uvx bump-my-version bump --new-version "$(VERSION)" patch
	@echo ""
	@echo "Bumped to $(VERSION). Edit CHANGELOG.md to fill the new section,"
	@echo "review with 'git diff', then run 'make release-publish'."

release-publish:
	@version="$$(awk -F '"' '/^version = / { print $$2; exit }' pyproject.toml)"; \
	init_version="$$(awk -F '"' '/^__version__ *= */ { print $$2; exit }' rest_framework_services/__init__.py)"; \
	if [ "$$version" != "$$init_version" ]; then \
		echo "Version mismatch: pyproject=$$version, __init__=$$init_version"; exit 1; \
	fi; \
	if git rev-parse "v$$version" >/dev/null 2>&1; then \
		echo "Tag v$$version already exists locally."; exit 1; \
	fi; \
	if [ -n "$$(git ls-remote --tags origin "v$$version")" ]; then \
		echo "Tag v$$version already exists on origin."; exit 1; \
	fi; \
	if ! git diff-index --quiet HEAD --; then \
		git add pyproject.toml rest_framework_services/__init__.py CHANGELOG.md && \
		git commit -m "Release $$version"; \
	fi && \
	git tag -a "v$$version" -m "$$version" && \
	branch="$$(git rev-parse --abbrev-ref HEAD)" && \
	git push origin "$$branch" "v$$version" && \
	echo "Pushed Release $$version + tag v$$version. Watch: https://github.com/Artui/djangorestframework-services/actions"
