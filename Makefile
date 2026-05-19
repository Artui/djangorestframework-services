.PHONY: help init test lint lint-fix format format-check type-check type-check-strict-fixtures deps-bump docs-serve docs-build release-bump release-publish release-publish-prepare release-publish-finalize

help:
	@echo "Available targets:"
	@echo "  init             Sync deps (all groups) and install pre-commit hooks"
	@echo "  test             Run pytest with coverage (100% required)"
	@echo "  lint             Run ruff check + ty check"
	@echo "  lint-fix         Auto-fix lint issues with ruff"
	@echo "  format           Format with ruff"
	@echo "  format-check     Verify formatting"
	@echo "  type-check       Run ty over the package"
	@echo "  type-check-strict-fixtures  Verify ty flags expected drift in tests/services/strict_drift_fixtures.py"
	@echo "  deps-bump        Upgrade pinned dependencies"
	@echo "  docs-serve       Live-reload docs at http://localhost:8000 (needs mkdocs.yml)"
	@echo "  docs-build       Build docs into ./site (strict — fails on broken links)"
	@echo "  release-bump     Bump version files + CHANGELOG. Usage: make release-bump VERSION=X.Y.Z"
	@echo "  release-publish  Run the full release flow locally (prepare + uv publish + finalize)"
	@echo "  release-publish-prepare   CI: extract version, no-op if already released, test, build dist"
	@echo "  release-publish-finalize  CI: tag, push tag, create GitHub Release with dist attached"

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

type-check-strict-fixtures:
	@expected="$$(grep -c '^# expect-error' tests/services/strict_drift_fixtures.py)"; \
	got="$$(uv run ty check tests/services/strict_drift_fixtures.py 2>&1 | grep -c '^error')"; \
	if [ "$$expected" != "$$got" ]; then \
		echo "Expected $$expected ty diagnostics in strict_drift_fixtures.py, got $$got."; \
		echo "Run 'uv run ty check tests/services/strict_drift_fixtures.py' to see the current diagnostics."; \
		exit 1; \
	fi; \
	echo "ty flagged $$got/$$expected expected drifts in tests/services/strict_drift_fixtures.py."

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
	uv lock
	@echo ""
	@echo "Bumped to $(VERSION). Edit CHANGELOG.md to fill the new section,"
	@echo "review with 'git diff', then run 'make release-publish'."

# Version source for scripts/release-publish.sh. `path|awk-pattern` selecting
# a `… = "X.Y.Z"` line. `pyproject.toml` declares `dynamic = ["version"]` and
# hatchling reads the value from `version.py` at build time, so there is one
# source of truth.
RELEASE_PACKAGE_NAME := djangorestframework-services
RELEASE_VERSION_FILES := rest_framework_services/version.py|^__version__[^=]*= *

release-publish:
	@PACKAGE_NAME='$(RELEASE_PACKAGE_NAME)' \
	VERSION_FILES="$$(printf '$(RELEASE_VERSION_FILES)')" \
		bash scripts/release-publish.sh all

release-publish-prepare:
	@PACKAGE_NAME='$(RELEASE_PACKAGE_NAME)' \
	VERSION_FILES="$$(printf '$(RELEASE_VERSION_FILES)')" \
		bash scripts/release-publish.sh prepare

release-publish-finalize:
	@PACKAGE_NAME='$(RELEASE_PACKAGE_NAME)' \
	VERSION_FILES="$$(printf '$(RELEASE_VERSION_FILES)')" \
		bash scripts/release-publish.sh finalize
