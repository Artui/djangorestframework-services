# CLAUDE.md

Conventions for writing code in this repo. For *what* the library does, see [`README.md`](README.md).

- Package on PyPI: `djangorestframework-services`
- Importable name: `rest_framework_services`
- Build backend: hatchling

---

## Commands

Use the Makefile targets for everything; CI and pre-commit both call them.

```bash
make init           # uv sync --all-groups + pre-commit install (first-checkout setup)
make test           # uv run pytest (with --cov=rest_framework_services --cov-fail-under=100)
make lint           # ruff check + ty check rest_framework_services
make lint-fix       # ruff check --fix
make format         # ruff format
make format-check   # ruff format --check --diff
make type-check     # ty check rest_framework_services
make docs-serve     # live-reload docs at http://localhost:8000 (needs mkdocs.yml)
make docs-build     # mkdocs build --strict (fails on broken links)
```

`make help` lists every target with a one-line description.

---

## Structural rules

These are non-negotiable. They are what keep the package navigable.

1. **One exported class or function per file.** The file is named after the exported symbol in `snake_case`. `MyClass` lives in `my_class.py`; `do_thing` lives in `do_thing.py`.
2. **Private helpers used only in one file** stay in that file with a `_name` prefix.
3. **Non-exported helpers shared across files in a package** go in that package's `utils.py`. Classes are allowed in `utils.py` if they're internal infrastructure (e.g. `MutationFlowMixin` lived there before being promoted to its own file when it became public).
4. **Top-level imports only.** Lazy / function-local imports are forbidden unless a circular import is diagnosed and proven, in which case document the reason inline.
5. **Full type annotations on every function and method signature.** `Any` is allowed at integration boundaries (DRF serializers, Django ORM); avoid it inside the package.
6. **Re-exports go in `__init__.py`.** Each package's `__init__.py` lists its public surface in `__all__`. The top-level `rest_framework_services/__init__.py` re-exports the user-facing API.

---

## Adding a new feature

A typical change touches three places:

1. The source file (one new `.py` per new class/function).
2. The package's `__init__.py` (add to imports + `__all__`).
3. A test file under `tests/` mirroring the source path (`rest_framework_services/foo/bar.py` → `tests/foo/test_bar.py`).

Then verify:

```bash
make lint-fix
make format
make test
```

`make test` enforces 100% line + branch coverage. New branches must be exercised, not annotated away.

If the new symbol is part of the public API, also add it to the top-level `rest_framework_services/__init__.py` and to `CHANGELOG.md` under `[Unreleased]`.

---

## Tests

- Live in `tests/`, mirroring the source tree. `tests/mutations/test_apply_input.py` tests `rest_framework_services/mutations/apply_input.py`.
- Each test file is named `test_<module_name>.py`.
- Async tests rely on `pytest-asyncio` (`asyncio_mode = "auto"` in `pyproject.toml`); just write `async def test_...`.
- DB-touching tests use `@pytest.mark.django_db`; async DB tests use `@pytest.mark.django_db(transaction=True)`.
- The minimal Django app for integration tests lives at `tests/testapp/`. Add models there only if a new test needs them; prefer reusing the existing ones.
- 100% coverage is enforced via `--cov-fail-under=100`. Branch coverage too. If a branch is genuinely unreachable, restructure rather than `# pragma: no cover`-ing.

---

## Type checking

`ty` is scoped to `rest_framework_services/` only — Django's dynamic descriptors (`Model.objects` in particular) trip the checker when it walks `tests/`. If `ty` complains about a piece of source code, fix the source rather than scoping the checker more narrowly.

Dev deps include `django-stubs` and `djangorestframework-stubs`, so DRF and Django types are known to `ty`.

Common patches you'll still need inside the package:

- In **standalone mixins** (classes that don't inherit from `GenericAPIView` directly), attributes like `self.request`, `self.kwargs`, `self.action`, `self.get_object` aren't in scope at definition time. Declare them at the top of the class as `attr: Any` (or a more specific type) with a comment naming the parent.
- `super()` calls in standalone mixins to methods that only arrive at concrete composition time (e.g. `get_queryset`, `get_object`, `get_serializer_class`) still require `# ty: ignore[unresolved-attribute]`. In concrete view classes that extend `GenericAPIView` directly, those calls are fully resolved by the stubs.
- `get_serializer_class()` overrides must return `type[BaseSerializer[Any]]` — that is what DRF's stubs declare as the return type of the base method.

`ty` runs on every commit via the pre-commit hook and in CI.

---

## Linting and formatting

- `ruff check` enforces `E`, `F`, `UP`, `B`, `SIM`, `I`, `TID` (see `[tool.ruff.lint]` in `pyproject.toml`).
- `ruff format` is the source of truth for layout. Don't fight it.
- The pre-commit hook runs `make lint-fix` (auto-applies safe fixes) and `make format` (rewrites). Commits should be clean before push; CI will reject otherwise.

---

## Imports inside the package

- Always absolute, fully qualified: `from rest_framework_services.exceptions.service_error import ServiceError`. No relative imports (`from .foo import Bar`).
- isort is wired through ruff (`I` rules). Order: stdlib → third-party → first-party. `rest_framework_services` and the test app are first-party.
- A package's `__init__.py` is the only file that re-exports; downstream files import from the leaf module, not from the package's `__init__`.

---

## Compatibility floor

| Axis | Floor | Tested ceiling |
|---|---|---|
| Python | 3.10 | 3.14 |
| Django | 4.2 | 6.0 |
| DRF | 3.14 | latest |

Don't use syntax or stdlib features newer than Python 3.10 (`match`, generics-without-import, etc. are fine; `type` statements from 3.12 are not). CI runs the full Python × Django product, so any combo-specific bug surfaces fast.

`from __future__ import annotations` is at the top of every `.py` file in the package that has annotations (i.e. everything except bare re-export `__init__.py` files). Keep it that way.

---

## CI and pre-commit

`.github/workflows/tests.yml` runs on every push to `main` and every PR:

- `lint` job — `ruff check`, `ruff format --check --diff`, `ty check rest_framework_services`.
- `docs` job — `mkdocs build --strict` (with `DJANGO_SETTINGS_MODULE` pointed at
  `tests.conftest_settings`, since `mkdocstrings` imports the package and DRF
  reads settings at import time). Broken links / missing pages fail fast.
- `test` job — matrix Python 3.10–3.14 × Django 4.2/5.0/5.1/5.2/6.0, with the
  Python × Django version-support exclusions baked in. Each cell pins Django via
  `uv pip install --reinstall-package django "django~=X.Y.0"` then runs `pytest`
  (which enforces `--cov-fail-under=100`).

`.github/workflows/release.yml` runs on push of a `vX.Y.Z` tag (see *Releasing*
below).

`.pre-commit-config.yaml` runs locally on every commit:

- `pre-commit-hooks` basics (trailing whitespace, EOL, YAML, large files)
- `make lint-fix`, `make format`, `make type-check`

If a hook fails, fix the underlying issue and create a **new** commit. Don't `--amend` to chase hook output (that risks losing prior work in pre-existing commits).

---

## Common pitfalls

- **Functions stored as class attributes get bound on instance access.** Use `views.utils.get_class_attr(self, "name")` to retrieve the unbound callable; never read service / selector callables via plain `self.attr`.
- **`DataclassSerializer` can't deduce a field type from `Any`.** Use proper types (`str | None = None` for partial-update fields). For the strict "omitted vs. None" distinction, use the `UNSET` sentinel and test with `is UNSET`.
- **`get_object()` is shared between retrieve and update/destroy.** When configuring a viewset that mixes a `retrieve_selector` with mutation actions, the selector applies to all of them — that's intentional. Override `get_object` yourself for action-specific lookup.
- **Don't add a `filter_dataclass` shape.** Filtering is DRF's job; use `filter_backends`. Selectors take whatever extra kwargs the user wires through `get_selector_kwargs()`.
- **Don't import from `rest_framework` inside `rest_framework_services.exceptions/`.** That module is the framework-agnostic boundary; the view layer maps exceptions to DRF responses.

---

## Releasing

The release pipeline is triggered by pushing a `vX.Y.Z` tag and runs three
sequential jobs in `.github/workflows/release.yml`:

1. `build` — re-runs the full test suite at 100% coverage, then `uv build`.
   It also asserts the git tag matches `rest_framework_services.__version__`,
   so you can never tag without bumping the source.
2. `publish-pypi` — uploads via **OIDC trusted publishing**. There is no API
   token in the repo; PyPI must have a Trusted Publisher configured for the
   project pointing at this workflow.
3. `publish-docs` — `mkdocs gh-deploy --force --clean` to the `gh-pages`
   branch. The job no-ops (without failing) if `mkdocs.yml` is absent.

### Cutting a release

Two Make targets wrap the whole flow. The bump itself is driven by
[bump-my-version](https://github.com/callowayproject/bump-my-version)
via `uvx`; configuration lives in `[tool.bumpversion]` in
`pyproject.toml`. No permanent dependency is added to the project.

```bash
# 1. Make sure CHANGELOG.md has the entries you want to ship under
#    ## [Unreleased]. Then bump:
make release-bump VERSION=0.4.1
# This rewrites pyproject.toml + __init__.py, promotes the [Unreleased]
# block under a dated [0.4.1] section, and rewrites the link footer —
# all driven by the [[tool.bumpversion.files]] entries in pyproject.toml.

# 2. Review the diff. Edit CHANGELOG.md if you want to reword anything.
git diff

# 3. Push:
make release-publish
# Commits the bump as "Release X.Y.Z", tags vX.Y.Z, and pushes both to
# origin. The tag push triggers .github/workflows/release.yml.
```

`release-bump` refuses to run on a dirty tree (bump-my-version's
`allow_dirty = false`), so you don't fold unrelated changes into the
release commit. `release-publish` refuses to run if `pyproject.toml`
and `rest_framework_services/__init__.py` disagree, or if the tag
already exists locally or on origin.

If you really need to do it by hand, the manual flow is: bump both
version files in lockstep, edit `CHANGELOG.md`, commit, then
`git tag -a vX.Y.Z -m "X.Y.Z"` and `git push origin main vX.Y.Z`.

### One-time setup (manual, by the repo owner)

These steps need to happen once before the first tag push will succeed:

1. **PyPI Trusted Publisher** — sign in to PyPI, open the project settings,
   add a publisher pointing at `Artui/djangorestframework-services`, workflow
   `release.yml`, environment `pypi`. (Use a "Pending" publisher if the project
   does not yet exist on PyPI; promote it after the first release.)
2. **GitHub Environment** — create a `pypi` environment under
   `Settings → Environments` (no secrets needed; OIDC handles auth).
3. **GitHub Pages** (only if you publish docs) — under `Settings → Pages`,
   set "Build and deployment" source to "Deploy from a branch", branch
   `gh-pages`, folder `/`. The first tag push that has a `mkdocs.yml` will
   create that branch.

### Manual fallback

If the workflow is unavailable, `uv build && uv publish` still works
(publishing needs a PyPI token in env or `~/.pypirc`). Prefer the tag-driven
path — it keeps tag, version, and PyPI in lockstep.
