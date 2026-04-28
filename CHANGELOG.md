# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.5.0] — 2026-04-28

### Added

- `SelectorSpec` (in `rest_framework_services.types`) — frozen dataclass
  bundling per-action read config: `selector` and `output_serializer`.
- `ActionSerializerResolver` viewset mixin — resolves
  `get_serializer_class()` from the active action's `action_specs` entry
  (`output_serializer` on either a `SelectorSpec` or `ServiceSpec`),
  replacing `MultiSerializerMixin`.
- `django-stubs` and `djangorestframework-stubs` added to dev dependencies
  so `ty` resolves Django/DRF types directly.

### Fixed

- `update_from_input` / `aupdate_from_input` now automatically include
  `auto_now=True` fields (e.g. `updated_at`) in the computed
  `update_fields` list when `update_fields=True` (default). Previously
  those columns were silently skipped because Django only updates
  `auto_now` fields when they are explicitly listed in `update_fields`.
  Explicit `update_fields=[...]` lists are still passed through
  unchanged; `update_fields=False` is unaffected.

### Changed

- **Breaking.** `service_specs` renamed to `action_specs` on all viewset
  mixins (`SelectorListMixin`, `SelectorRetrieveMixin`,
  `ServiceCreateMixin`, `ServiceUpdateMixin`, `ServiceDestroyMixin`) and
  on `ServiceViewSet` / `SelectorViewSet`.
- **Breaking.** Read-side entries in `action_specs` (`"list"`,
  `"retrieve"`) now require a `SelectorSpec` instance. Bare callables are
  no longer accepted and raise `ImproperlyConfigured` at request time with
  a migration message.
- **Breaking.** `serializer_classes` mapping and `MultiSerializerMixin`
  removed. Move per-action serializers into `action_specs` via
  `SelectorSpec(output_serializer=...)` or `ServiceSpec(output_serializer=...)`.
- **Breaking.** `SelectorListView` and `SelectorRetrieveView` now accept
  a single `spec: SelectorSpec` attribute instead of separate `selector`
  and `serializer_class` attributes. `spec.output_serializer` overrides
  `get_serializer_class()`; `spec.selector = None` keeps DRF's default
  `get_queryset()` / `get_object()`.
- A wrong-type `action_specs` entry (e.g. `SelectorSpec` on a write
  action, or `ServiceSpec` on a read action) now raises
  `ImproperlyConfigured` at request time instead of silently falling back.

### Migration from 0.4.x

| 0.4.x | 0.5.x |
|---|---|
| `service_specs = {...}` | `action_specs = {...}` |
| `serializer_classes = {"list": S}` | `action_specs["list"] = SelectorSpec(output_serializer=S)` |
| `service_specs["list"] = list_fn` | `action_specs["list"] = SelectorSpec(selector=list_fn)` |
| `service_specs["list"] = list_fn` + `serializer_classes["list"] = S` | `action_specs["list"] = SelectorSpec(selector=list_fn, output_serializer=S)` |
| `class V(SelectorListView): selector = fn; serializer_class = S` | `class V(SelectorListView): spec = SelectorSpec(selector=fn, output_serializer=S)` |
| `MultiSerializerMixin` in MRO | `ActionSerializerResolver` in MRO |

## [0.4.0] — 2026-04-28

### Added

- mkdocs-material documentation site under `docs/`, published to GitHub
  Pages on every tag release. Covers quickstart, concepts, mutation
  helpers, errors & atomic, async, recipes, and an autodoc reference
  section driven by `mkdocstrings`.
- Tag-driven release pipeline (`.github/workflows/release.yml`): on
  push of a `vX.Y.Z` tag, runs the test suite, asserts the tag matches
  both `pyproject.toml` and `__version__`, builds wheel + sdist,
  publishes to PyPI via OIDC trusted publishing, then deploys docs to
  the `gh-pages` branch.
- `docs` job in `tests.yml` runs `mkdocs build --strict` on every PR
  to catch broken links / missing autodoc targets before merge.
- `make help`, `make init`, `make docs-serve`, `make docs-build`
  Makefile targets.

### Changed

- `tests.yml` modernised: switched to `astral-sh/setup-uv@v6`, added a
  cancel-in-progress concurrency group, and split lint / docs / test
  into separate jobs.
- `CLAUDE.md` Releases section now describes the tag-driven pipeline
  and the one-time PyPI Trusted Publisher / GitHub Pages setup.

## [0.3.0] — 2026-04-27

### Added

- `ServiceSpec` (in `rest_framework_services.types`) — frozen dataclass
  bundling per-action mutation config: `service`, `input_serializer`,
  `output_serializer`, `output_selector`, `atomic`, `success_status`.
- `service_specs` class attribute on `ServiceViewSet` (and the per-action
  mixins) — a single action-keyed mapping replacing the per-action flat
  attributes. Read actions (`"list"`, `"retrieve"`) accept a bare callable
  (the selector); write actions (`"create"`, `"update"`, `"destroy"`)
  accept a `ServiceSpec`.

### Changed

- **Breaking.** Removed flat per-action attributes from viewset mixins:
  `list_selector`, `retrieve_selector`, `create_service`,
  `create_input_serializer`, `create_output_serializer`,
  `create_output_selector`, `create_atomic`, and the matching `update_*`
  / `destroy_*` triplets. Move the values into `service_specs` instead.
- **Breaking.** Standalone mutation views (`ServiceCreateView`,
  `ServiceUpdateView`, `ServiceDeleteView`) no longer accept individual
  flat attributes (`service`, `input_serializer`, …). They are now
  configured by setting a single `spec` class attribute to a
  `ServiceSpec`.
- **Breaking.** `@service_action` now takes a `ServiceSpec` as its first
  positional argument instead of `service=`/`input_serializer=`/etc.
  kwargs. DRF-action options (`detail`, `methods`, `url_path`,
  `url_name`, plus extras) are unchanged.

## [0.2.0] — 2026-04-27

### Changed

- Renamed `input_dataclass` → `input_serializer` everywhere it's configured
  (mutation views, viewset mixins, `@service_action`). The attribute now
  accepts a bare dataclass type (wrapped in `DataclassSerializer` on the
  fly), a `DataclassSerializer` subclass, or any other `Serializer`
  subclass (e.g. `ModelSerializer`).
- Services now receive the serializer's `validated_data` as `data` (a
  dataclass instance for dataclass-based serializers, a `dict` for plain
  `Serializer` / `ModelSerializer` subclasses) instead of the result of
  `serializer.save()`. Persistence is the service's responsibility.

## [0.1.0] — 2026-04-27

First public release. A service / selector layer for Django REST Framework:
views, viewsets, mutation helpers, and a scaffolding command, with
first-class sync + async support and 100% test coverage.

### Added

#### Mutation helpers (`rest_framework_services.mutations`)

- `apply_input(instance, data, ...)` — set attributes in memory, no save.
- `create_from_input(model, data, ...)` — build, save, optional M2M.
- `update_from_input(instance, data, ...)` — diff in-memory state vs. input,
  call `save(update_fields=[...])` with only the fields that actually
  changed. Defaults can be overridden per call.
- `acreate_from_input` / `aupdate_from_input` — async siblings using Django
  4.2+ `asave()` / `aset()`.
- All accept `data` as a dataclass / dict / `__dict__`-bearing object, with
  `field_map`, `exclude_fields`, and `m2m` kwargs.
- All return a `ChangeResult` (frozen dataclass) carrying `instance`,
  `created`, and a tuple of `FieldChange` records, plus a `changed_fields`
  property and `get_field_change(name)` lookup.
- The `UNSET` sentinel distinguishes "field omitted from input" from "field
  explicitly set to `None`".

#### Standalone views (`rest_framework_services.views`)

- `ServiceCreateView`, `ServiceUpdateView`, `ServiceDeleteView` — single-
  purpose `GenericAPIView` subclasses, each composing `MutationFlowMixin`.
  Configure via `service`, `input_serializer`, `output_serializer`,
  `output_selector`, `atomic`, `success_status`.
- `SelectorListView`, `SelectorRetrieveView` — built on DRF's
  `ListModelMixin` / `RetrieveModelMixin`. `selector` overrides
  `get_queryset()` / `get_object()`; everything else (filter backends,
  pagination, serialization) is vanilla DRF.

#### Viewsets (`rest_framework_services.viewsets`)

- `ServiceViewSet` — full-CRUD viewset composed of `ServiceCreateMixin`,
  `ServiceUpdateMixin`, `ServiceDestroyMixin`, `SelectorListMixin`,
  `SelectorRetrieveMixin`, and `MultiSerializerMixin`.
- `SelectorViewSet` — read-only viewset (list + retrieve only).
- All per-action mixins are exported so you can compose only the actions
  you need.
- `MultiSerializerMixin` — per-action serializer dispatch via a single
  `serializer_classes: dict[str, type[Serializer]]` mapping; falls back to
  DRF's `serializer_class` when the action isn't mapped.
- `MutationFlowMixin` — exported as the building block for service-backed
  action flow on bespoke shapes that don't fit the existing five mixins.
- `@service_action` — decorator wrapping DRF's `@action` and routing the
  custom action through the same validate-dispatch-render flow as the
  standard mutations.

#### Selector protocols (`rest_framework_services.selectors`)

- `Selector` and `AsyncSelector` — `runtime_checkable` Protocols for
  type-safe documentation of selector callables.

#### Shared value types (`rest_framework_services.types`)

- `ChangeResult`, `FieldChange`, `UNSET`. Framework-agnostic; live in their
  own package and are re-exported at the top level.

#### Exceptions (`rest_framework_services.exceptions`)

- `ServiceError` and `ServiceValidationError` — framework-agnostic
  exceptions raised from service code. The view boundary translates them:
  `ServiceValidationError` → DRF `ValidationError` (HTTP 400); `ServiceError`
  → `APIException` (HTTP 422).
- Services do not import from `rest_framework`.

#### Sync + async dispatch

- Services and selectors can be `async def`; `inspect.iscoroutinefunction`
  detects them and the view bridges to `async_to_sync` under sync
  dispatch.
- Atomic transactions wrap async services via
  `sync_to_async(thread_sensitive=True)` to keep ORM connection state on a
  consistent thread.

#### Atomic transactions

- Every mutation service runs inside `transaction.atomic()` by default.
- Opt-out per view via `atomic = False` (or per-action via
  `create_atomic` / `update_atomic` / `destroy_atomic`).

#### `startserviceapp` management command

- Subclass of `django.core.management.templates.TemplateCommand`.
- Scaffolds a service-oriented Django app with `services/`, `selectors/`,
  `validators/`, `serializers/`, `utils/` as packages, plus `models/` and
  `views/` as packages instead of single files.
- The bundled template lives at
  `rest_framework_services/management/templates/service_app/` and ships
  in the wheel. Add `"rest_framework_services"` to `INSTALLED_APPS` to
  make the command discoverable.

#### Examples and documentation

- `README.md` — quick start (DataclassSerializer-first, ModelSerializer
  shown as alternative), mental model, mutation helpers, views, viewsets,
  errors, atomic, async, `startserviceapp`.
- `examples/` — runnable Django project with an invoices app demonstrating
  the full surface and an end-to-end `APITestCase`.

### Compatibility

- Python 3.10 – 3.14
- Django 4.2, 5.0, 5.1, 5.2, 6.0
- Django REST Framework ≥ 3.14
- `djangorestframework-dataclasses` (hard dependency)

### Quality gates

- 100% line + branch coverage enforced via `pytest-cov`.
- Type-checked with [`ty`](https://github.com/astral-sh/ty).
- Linted and formatted with [`ruff`](https://github.com/astral-sh/ruff).
- CI matrix runs the full Python × Django product on every push.

[Unreleased]: https://github.com/Artui/djangorestframework-services/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/Artui/djangorestframework-services/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/Artui/djangorestframework-services/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/Artui/djangorestframework-services/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Artui/djangorestframework-services/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Artui/djangorestframework-services/releases/tag/v0.1.0
