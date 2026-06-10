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
4. **Top-level imports only.** Lazy / function-local imports are forbidden unless a circular import is diagnosed and proven, *or* the import targets a proven optional dependency (declared in `[project.optional-dependencies]` and gated behind an explicit `enable_*()` opt-in helper). In either case document the reason inline. Canonical optional-dep example: `rest_framework_services/openapi/enable_openapi.py`.
5. **Full type annotations on every function and method signature.** `Any` is allowed at integration boundaries (DRF serializers, Django ORM); avoid it inside the package.
6. **Re-exports go in `__init__.py`.** Each package's `__init__.py` lists its public surface in `__all__`. The top-level `rest_framework_services/__init__.py` re-exports the user-facing API.

---

## Adding a new feature

**Always work on a dedicated branch.** Any new feature, bugfix, or version-bump
work happens on a freshly-cut branch — never on `main` directly. Push to that
branch and open a PR. `main` only ever advances through merges of reviewed
PRs (or, for releases, the `Release X.Y.Z` commit pushed by
`make release-publish` once the release branch has been merged).

```bash
git switch -c feature/short-name   # before you start
# ... edits, commits ...
git push -u origin feature/short-name
gh pr create
```

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
- **Use `...` (Ellipsis) instead of `pass` for empty function and class bodies.** Includes Protocol stubs, test fixtures, no-op viewset action methods stubbed out for `@service_action`, and bare placeholder classes. `pass` is reserved for intentionally-empty *statement* contexts (e.g. `try: ... except FooError: pass`) where Ellipsis would read as a literal expression rather than a no-op.

---

## Imports inside the package

- Always absolute, fully qualified: `from rest_framework_services.exceptions.service_error import ServiceError`. **Never** relative imports — no `from . import x`, no `from .foo import Bar`, no `from ..pkg import baz`. This applies inside the package and inside `tests/` (use `from tests.testapp.models import …`).
- isort is wired through ruff (`I` rules). Order: stdlib → third-party → first-party. `rest_framework_services` and the test app are first-party.
- A package's `__init__.py` is the only file that re-exports; downstream files import from the leaf module, not from the package's `__init__`.
- **`types/` is the dependency sink.** Value-shape carriers (dataclasses, generic specs, sentinels) live there. `views/`, `selectors/`, `services/`, `viewsets/` may import *from* `types/`; `types/` must **not** import from any of them. If a `types/` member needs to reference a Protocol or other shape currently living elsewhere — e.g. a spec field annotation — move that shape into `types/` first (this is why `ServiceView` lives in `types/` rather than `views/`). Behavioural Protocols *not* referenced from `types/` (e.g. `CreateService`, `ListSelector`) stay in their behavioural package — that is their natural home, and putting everything in `types/` would muddle the boundary between value shapes and callable contracts.
- **Don't eagerly import Django models** (`django.contrib.auth.models`, `django.db.models.<concrete>`, etc.) at package-import time. `rest_framework_services` ships in `INSTALLED_APPS`, so the package gets imported during `apps.populate()`, before app models are loaded — eager imports raise `AppRegistryNotReady`. If you need a precise type, prefer `Any` for that field (the framework treats `request.user` as an integration boundary; see `services/utils.py:UserT`), or move the import into a non-eagerly-loaded module.

---

## Compatibility floor

| Axis | Floor | Tested ceiling |
|---|---|---|
| Python | 3.10 | 3.14 |
| Django | 4.2 | 6.0 |
| DRF | 3.14 | latest |

Python 3.10 syntax / stdlib features are fair game (`match` statements, PEP 604 union syntax, etc.). Newer stdlib symbols (`Unpack`, `NotRequired`, `assert_type`, `TypeVar` with `default=...`, `override`) must be imported from `typing_extensions` so 3.10 / 3.11 keep working — `typing_extensions` re-exports them on every supported version. PEP 728's `TypedDict(..., extra_items=...)` is deliberately not used (no `typing_extensions` backport, blocks mypy). PEP 695 `class C[T]:` / `def f[T](...)` / `type X = …` syntax is also avoided — the package consistently uses `TypeVar(...)` + `Generic[T]`; don't "modernise" existing TypeVar uses on sight (the `UP040`, `UP046`, `UP047` ruff rules are ignored project-wide). CI runs the full Python × Django product, so any combo-specific bug surfaces fast.

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
- **`get_object()` is shared between retrieve and update/destroy.** When configuring a viewset that mixes a `retrieve_selector` with mutation actions, the selector applies to all of them — that's intentional. For an action-specific lookup, set `instance_selector_spec` on that action's `ServiceSpec` (it takes precedence over the `get_object()` chain); overriding `get_object` yourself remains the catch-all escape hatch.
- **Don't add a `filter_dataclass` shape.** Filtering is DRF's job; use `filter_backends`. Selectors take whatever extra kwargs the user wires through `get_selector_kwargs()`.
- **Don't import from `rest_framework` inside `rest_framework_services.exceptions/`.** That module is the framework-agnostic boundary; the view layer maps exceptions to DRF responses.
- **Adding a view that dispatches a `ServiceSpec`?** Don't reassemble the kwargs pool or call `_execute_mutation` directly — go through `views.mutation.utils.dispatch_mutation_for_spec` (mutations) or `selectors.utils.dispatch_selector_for_spec` (selectors). Centralising the call shape keeps the kwargs-resolution chain (`spec.kwargs` → `get_<action>_*_kwargs` → `get_*_kwargs`) consistent. Also hook fail-fast validation into `as_view()` via `views.spec_validation.validate_mutation_view_spec` / `validate_selector_view_spec`.
- **`# ty: ignore[<rule>]`, not `# type: ignore[<rule>]`.** ty has its own pragma syntax and ignores mypy-style comments. The most common rule names are `unresolved-attribute` (for stub-incomplete super calls and dynamic attributes like the `_service_spec` stamp on `@service_action`) and `unused-ignore-comment`.

---

## Releasing

The release pipeline is **merge-to-main triggered**, not tag-triggered.
`.github/workflows/release.yml` runs on every push to `main` and calls
`make release-publish-prepare`. The script in
[`scripts/release-publish.sh`](scripts/release-publish.sh) is the single source
of truth for the flow and behaves as follows:

1. Extract the version from `rest_framework_services/version.py` (the
   single source of truth; `pyproject.toml` declares `dynamic = ["version"]`
   and hatchling reads the value from `version.py` at build time).
2. Check whether `vX.Y.Z` already exists locally or on origin. **If yes →
   short-circuit:** emit `released=false` to `$GITHUB_OUTPUT` and exit 0.
   That is what makes every-merge-to-main safe: non-bumping merges are no-ops.
3. Run `uv run pytest` as a final gate.
4. `uv build` into `dist/`.
5. Extract the `## [X.Y.Z]` section from `CHANGELOG.md` into
   `dist/release-notes.md`.
6. Emit `released=true` so the downstream steps run.

If `released=true`, the workflow then:

- Publishes to PyPI via **OIDC trusted publishing**
  (`pypa/gh-action-pypi-publish`, no token in repo).
- Calls `make release-publish-finalize`, which tags `vX.Y.Z`, pushes the tag,
  and runs `gh release create vX.Y.Z --notes-file dist/release-notes.md
  dist/*.whl dist/*.tar.gz`.
- `mkdocs gh-deploy --force --clean` to `gh-pages` (skipped if `mkdocs.yml`
  is missing).

The previous tag-trigger flow has been removed.

### Cutting a release

The bump itself is driven by
[bump-my-version](https://github.com/callowayproject/bump-my-version) via `uvx`;
configuration lives in `[tool.bumpversion]` in `pyproject.toml`.

```bash
# 1. On a release branch, make sure CHANGELOG.md has the entries you want
#    to ship under ## [Unreleased]. Then bump:
make release-bump VERSION=0.4.1
# This rewrites rest_framework_services/version.py (the single source of
# truth — pyproject.toml is dynamic and reads from it at build time),
# promotes the [Unreleased] block under a dated [0.4.1] section, and
# rewrites the link footer — all driven by the [[tool.bumpversion.files]]
# entries in pyproject.toml.

# 2. Review the diff, commit, open a PR, get it reviewed.
git diff
git commit -am "Release 0.4.1"
git push -u origin release/0.4.1
gh pr create

# 3. Merge to main. release.yml fires on the merge commit, detects the
#    bumped version, runs the full flow, and tags/publishes vX.Y.Z.
```

`release-bump` refuses to run on a dirty tree (bump-my-version's
`allow_dirty = false`), so you don't fold unrelated changes into the
release commit.

For an end-to-end workstation release (e.g. publishing from a developer
machine when CI is broken), `make release-publish` runs prepare → `uv publish`
→ finalize in one shot. Set `DRY_RUN=1` to rehearse without uploading or
pushing.

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
