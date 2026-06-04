# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- A `destroy`/`DELETE` `ServiceSpec` with a custom `success_status` whose
  service returns `None` now renders an **empty body at the configured
  status**. Previously the update-in-place fallback misfired (it keyed off
  "status ≠ 204" as a proxy for "this is an update"), surfacing the stale
  post-delete instance as the response body — which then failed to serialize.
  The fallback is now driven by an explicit update-vs-destroy intent flag.
- No-output mutations (no `output_selector_spec`) whose service returns `None`
  now always render an empty body instead of attempting to serialize the raw
  in-memory model instance. This makes no-input/no-output `update` and
  `destroy` services work as expected. An explicitly-set `spec.success_status`
  is honored for the empty-body response; otherwise it falls back to `204`. A
  selector that returns `None` still renders `204` (its result is
  authoritative).

## [0.15.0] — 2026-06-02

### Added

- Output serializer-context providers can now receive the resolved data
  about to be serialized, so they can populate context from a **single**
  batched query instead of re-fetching. A provider (the
  `get_output_serializer_context` directional hook, a
  `get_<action>_output_serializer_context` per-action hook, or a spec-level
  `output_serializer_context`) may declare an extra keyword parameter —
  `result` (mutation output), `instance` (retrieve), or `page` (list) — and
  it is passed only when declared. `view` and `request` stay positional, so
  existing `(view, request)` providers are unaffected. The output context is
  now resolved *after* the service and output selector run, guaranteeing the
  value reflects exactly what will be rendered. See the
  [serializer-context recipe](https://artui.github.io/djangorestframework-services/recipes/serializer-context/).

## [0.14.0] — 2026-05-31

### Added

- `UnsetType` is now part of the public API (re-exported from
  `rest_framework_services`). It is the type of the `UNSET` sentinel, exported
  so callers can annotate sentinel-defaulted fields cleanly —
  `bio: str | None | UnsetType = UNSET` — instead of suppressing the
  type-checker with `# type: ignore[assignment]`. The class was previously
  private (`_Unset`); it has been renamed and promoted (the old name was never
  exported, so this is not a breaking change).

## [0.13.0] — 2026-05-22

### Changed (breaking)

- `SelectorSpec` is now keyword-only and requires a `kind: SelectorKind` field
  (`SelectorKind.LIST` or `SelectorKind.RETRIEVE`). The kind tells the
  dispatcher whether to materialize the selector return as a list or as a
  single retrieve-shaped instance, and it is cross-checked against the
  mount point at `as_view()` time — a `LIST` spec on `SelectorRetrieveView`
  (or the `"retrieve"` `action_specs` entry) raises `ImproperlyConfigured`
  fail-fast. Making the kind explicit lets a spec be reused outside an HTTP
  request (management command, cron job, non-DRF caller) without the
  semantics living implicitly in the call site.
- `ServiceSpec`'s output pipeline is collapsed into a single nested field:
  `output_selector_spec: SelectorSpec | None`. The previous flat fields
  `output_selector`, `output_serializer`, `output_serializer_context`,
  `select_related`, `prefetch_related`, `annotations`, and
  `extend_queryset` are removed; they now live on the nested spec (whose
  `kind` must be `SelectorKind.RETRIEVE`). The mutation dispatch path
  validates and consumes the nested spec, and `ActionSerializerResolver`
  reads the response serializer from `output_selector_spec.output_serializer`.
- `OutputSelector` Protocol removed. The "post-mutation re-fetch" selector
  is now structurally a `RetrieveSelector` (nested under
  `ServiceSpec.output_selector_spec`) and the `result` kwarg, when needed,
  is declared directly on the user's function signature.
- `@selector_action`'s `detail=` parameter is removed. The DRF URL shape
  is pinned from `spec.kind` (`LIST → detail=False`, `RETRIEVE →
  detail=True`) — there is no longer a way to override it. If you need a
  URL shape that doesn't match the response shape (a detail action that
  returns a list, or a collection action that returns a single resource),
  fall back to DRF's plain `@action` and write the dispatch yourself.
  `@service_action` still takes `detail=` (mutation routing is decoupled
  from the read-shape distinction).
- The internal `dispatch_retrieve_selector` helper is folded into
  `dispatch_selector_for_spec`, which now branches on `spec.kind`. Callers
  outside the library should not be affected (the helpers live under
  `selectors/utils.py` and were never part of the public API), but the
  symbol no longer exists.

## [0.12.0] — 2026-05-20

### Added

- `permission_classes` field on `ServiceSpec` and `SelectorSpec`. Accepts a
  sequence of DRF `BasePermission` subclasses to override the calling view's
  class-level `permission_classes` for the action the spec backs. `None`
  (the default) inherits the view-level permissions; an empty sequence means
  "no permissions" explicitly. Honored by `_ActionSpecsMixin.get_permissions`
  (covers all viewset mixins and `ServiceViewSet` / `SelectorViewSet`), by
  `MutationFlowMixin.get_permissions` (standalone mutation views), by the
  selector views' new `get_permissions` overrides, and by the
  `@service_action` / `@selector_action` decorators which forward the value
  into DRF's `@action(permission_classes=...)`. Misconfigurations (non-
  `BasePermission` subclasses, permission instances rather than classes)
  fail fast at `as_view()` time with `ImproperlyConfigured`.
- `input_serializer_context` and `output_serializer_context` fields on
  `ServiceSpec`, plus `output_serializer_context` on `SelectorSpec`. Each
  takes a `Callable[[ServiceView, Request], Mapping[str, Any]]` returning
  extra context keys to merge into the serializer's `context=` dict. They
  sit at the most-specific layer of the existing resolution chain
  (DRF default → directional `get_<direction>_serializer_context` hook →
  per-action `get_<action>_<direction>_serializer_context` hook → spec
  callable), so the spec wins on overlapping keys. Wired through
  `dispatch_mutation_for_spec` (input + output), `selector_action`
  (output), and a new `get_serializer_context` override on
  `_ActionSpecsMixin` and the standalone `SelectorListView` /
  `SelectorRetrieveView` so the spec's output context is honored by
  `ListModelMixin` / `RetrieveModelMixin` dispatch. Closes the gap where
  the selector list/retrieve mixins previously ignored the per-action
  `get_<action>_output_serializer_context` hook entirely.
- Per-spec queryset shaping on **both** `SelectorSpec` and `ServiceSpec`.
  Four new fields cover the static and dynamic cases:
  - `select_related: Sequence[str] | None` — forwarded to
    `qs.select_related(*spec.select_related)`.
  - `prefetch_related: Sequence[str | Prefetch] | None` — forwarded to
    `qs.prefetch_related(*spec.prefetch_related)`; accepts plain names or
    full `Prefetch` objects.
  - `annotations: Mapping[str, Any] | None` — merged into a single
    `qs.annotate(**spec.annotations)` call.
  - `extend_queryset: Callable[[QuerySet, ServiceView, Request], QuerySet] |
    None` — dynamic escape hatch, invoked *after* the declarative fields
    so it always sees the fully statically-shaped queryset. Synchronous
    only (no DB I/O — manipulates the lazy expression tree).

  On `SelectorSpec`, shaping runs inside `dispatch_selector_for_spec`, so
  both list and retrieve flows pick it up. On `ServiceSpec`, shaping
  applies to the QuerySet returned by `output_selector` — a typical
  pattern is `output_selector=lambda result: Model.objects.filter(pk=result.pk)`
  with the spec declaring the eager-loading. After shaping, a QuerySet
  return is materialized via `.first()` (the matching `dispatch_retrieve_selector`
  behaviour also added below).

  Configuring shaping with no `selector` (on `SelectorSpec`) or no
  `output_selector` (on `ServiceSpec`) raises `ImproperlyConfigured` at
  `as_view()` time. A non-QuerySet return when shaping is set raises at
  request time.
- `dispatch_retrieve_selector` now materializes a QuerySet return via
  `.first()`, so retrieve selectors can return a filtered QuerySet and
  let the framework apply shaping before pulling the single object.
  Backward-compatible — selectors that already returned an instance
  continue to work unchanged.

### Changed

- The field order on `ServiceSpec` and `SelectorSpec` has been reorganized
  to group related fields together: the callable, then the input pipeline
  (`input_*`), then the output pipeline (`output_*` + the new shaping
  fields), then the cross-cutting `kwargs` and `permission_classes`.
  Backward-compatible for keyword-argument call sites (which is every
  documented usage); positional construction must follow the new order.

## [0.11.0] — 2026-05-19

### Added

- Default model service factories — six new top-level exports that
  return ready-made service callables for the common case where the
  entire body is a one-line wrapper over the mutation helpers:
  `create_model(Model, *, field_map=, exclude_fields=, m2m=)`,
  `update_model(Model, *, field_map=, exclude_fields=, m2m=,
  update_fields=)`, and `delete_model(Model, *, soft_delete=)`, plus
  the matching `acreate_model` / `aupdate_model` / `adelete_model`
  async variants that wrap `acreate_from_input` / `aupdate_from_input`
  / `await instance.adelete()`. `m2m` accepts either a static mapping
  or a callable receiving the validated `data` — the typical shape
  when M2M values live on the input itself. The returned callables
  conform to the unified `CreateService` / `UpdateService` /
  `DeleteService` Protocols, so they absorb arbitrary framework-pool
  keys (`request`, `user`, URL kwargs, `ServiceSpec.kwargs` returns)
  and the existing view layer routes them — sync or async — without
  changes.

### Removed

- The pre-merge `StrictCreateService` / `StrictUpdateService` /
  `StrictDeleteService` / `StrictListSelector` / `StrictRetrieveSelector`
  / `StrictOutputSelector` classes. Rename every import to its unified
  equivalent (`StrictCreateService` → `CreateService`, etc.) and drop
  the trailing `ExtraT` type argument from each call site (extras are
  now typed on the function signature instead — see *Changed* below).
  The `@implements(...)` decorator pattern keeps working unchanged
  once the names update.
- `NoKwargs` (the empty `TypedDict` previously used as the `ExtraT`
  slot of strict service Protocols). The slot no longer exists. Drop
  imports of `NoKwargs`; if you were also writing
  `**extras: Unpack[NoKwargs]` in a service body, replace it with
  `**extras: Any`.

### Changed

- `ChangeResult` is now generic over the model type. The four mutation
  helpers (`create_from_input`, `acreate_from_input`,
  `update_from_input`, `aupdate_from_input`) thread the model TypeVar
  through their signatures, so `create_from_input(Author, ...)` returns
  a `ChangeResult[Author]` whose `.instance` is typed as `Author` —
  removing the `cast(Author, result.instance)` boilerplate that used to
  be necessary. Bare `ChangeResult` (no parameter) keeps resolving to
  `ChangeResult[Model]`, so existing annotations continue to work
  unchanged. Runtime behaviour is identical; this is a typing-only
  change.
- **Breaking** (typing only): the lenient and strict service / selector
  Protocols are merged into a single shape per kind, with `**extras`
  typed as `Any`. The strict form's trailing `ExtraT` type argument is
  gone. Names and call sites:
  - `CreateService[InputT, ResultT]`.
  - `UpdateService[InputT, InstanceT, ResultT]`.
  - `DeleteService[InputT, InstanceT, ResultT]`.
  - `ListSelector[ResultT]`.
  - `RetrieveSelector[ResultT]`.
  - `OutputSelector[InT, OutT]`.

  Strict-typed extras stay possible on your *own* function signature:
  declare a `TypedDict` with `total=False` (or per-field `NotRequired`)
  and annotate `**extras: Unpack[YourKw]`. Inside the function body,
  `extras["foo"]` is typed by `YourKw`. The Protocol does not enforce
  a kwargs-shape match — that cross-check only worked under one minor
  version of one type checker (`ty` 0.0.32) and never under `mypy` or
  `pyright`. Putting the typing on the function instead works on every
  modern checker.

  Three migration notes:

  1. Drop the trailing `ExtraT` from every parameterised call site:
     `StrictCreateService[AuthorIn, MyKw, Author]` →
     `CreateService[AuthorIn, Author]`. Keep `**extras: Unpack[MyKw]`
     on your function for typed extras.
  2. Strict extras `TypedDict`s must declare keys as `NotRequired`
     (or set `total=False` on the class) — required keys would make
     the function reject callers that omit them, breaking Protocol
     conformance under PEP 692.
  3. The lenient Protocols no longer name `request` and `user` as
     fixed parameters — they flow through `**extras` like any other
     framework-pool key (matching the strict Protocols, which already
     dropped these in 0.9.0). Services that declared
     `def fn(*, data, request, user, **kwargs)` keep working at
     runtime; to satisfy the new Protocol annotation either drop the
     named `request` / `user` parameters and read them off `**extras`,
     or subclass `HttpExtras[YourUser]` (now `total=False`) and use
     it as the `Unpack` target.

  Runtime behaviour of every callable is unchanged. The merge unblocks
  the default model service factories above and restores Python 3.10+
  support (the previous design needed PEP 728's `extra_items=Any`,
  which is not in mypy yet and forced a 3.13 floor).
- `HttpExtras[UserT]` is now declared `total=False`: every key is
  optional, matching the framework's runtime contract (the kwargs
  pool may or may not contain `request` / `user` depending on the
  caller). Subclass with `total=False` (or annotate fields as
  `NotRequired`) for the same reason — see migration note 2 above.
- Version is now tracked in a single source of truth at
  `rest_framework_services/version.py`. `pyproject.toml` declares
  `dynamic = ["version"]` and hatchling reads the value from `version.py`
  at build time. `rest_framework_services.__version__` continues to
  re-export the same value. Pure refactor — no behaviour change.

## [0.10.0] — 2026-05-03

### Added

- DRF-style serializer context propagation for both input and output
  serializers in service-backed views, viewset mixins, `@service_action`,
  and `@selector_action`. Three layers, later wins on overlap: DRF's
  `get_serializer_context()` → directional fallback
  (`get_input_serializer_context` / `get_output_serializer_context`,
  defaulting to `get_serializer_context()`) → per-action override
  (`get_<action>_input_serializer_context` /
  `get_<action>_output_serializer_context`, viewsets only). Standalone
  `SelectorListView` / `SelectorRetrieveView` already received DRF
  context through `self.get_serializer(...)` and continue to do so.

## [0.9.0] — 2026-05-03

### Added

- `HttpExtras[UserT]` — generic `TypedDict` for strict services that want
  `request` / `user` from the framework pool. Subclass it (with your own
  user model as the parameter) instead of redeclaring those keys on every
  `ExtraT`. `default=Any` keeps the unparameterised form ergonomic.
- `call_service` / `acall_service` and `call_selector` / `acall_selector`
  — HTTP-scope helpers for invoking a service or selector from another
  view, middleware, or custom action. They build the framework's kwargs
  pool (deriving `user` from `request.user`) and dispatch via the same
  signature filter the framework uses internally. Async callables are
  bridged transparently via `async_to_sync` (sync helper) or awaited
  (async helper). `request` is required by type — outside HTTP scope,
  call the service callable directly.
- `@selector_action` — the GET-side companion to `@service_action`.
  Wraps a viewset method with selector dispatch (collection or detail),
  honours `spec.output_serializer` when set, falls back to
  `view.get_serializer(...)`, and integrates with DRF pagination on the
  collection path. `ObjectDoesNotExist` / `None` map to 404 on detail.
- `startserviceapp` now scaffolds a `specs/` package alongside
  `services/` and `selectors/` — a conventional home for
  `ServiceSpec` / `SelectorSpec` instances.

### Changed

- **Breaking** (typing only): the strict service / selector Protocols
  no longer include `request` and `user` in their fixed signatures.
  `request` and `user` are still placed in the kwargs pool by the
  framework — services that read them now declare them on their
  `ExtraT` `TypedDict` (most cleanly via `HttpExtras[YourUser]`), or
  omit them entirely if unused.
  - Migration: replace
    `def fn(*, data, request: Request, user: UserT, **extras: Unpack[MyKw])`
    with either
    `def fn(*, data, **extras: Unpack[MyKw])` (drop the params) or
    `class MyKw(HttpExtras[YourUser]): ...` followed by
    `def fn(*, data, **extras: Unpack[MyKw])` (extras carries
    `request` / `user`).
  - Lenient Protocols are unchanged — `**kwargs: Any` already lets
    services omit `request` / `user`.
- Stale doc reference removed: `docs/recipes/service-action.md` no
  longer lists `view` as a pool key (it was never in the pool — see
  `views/mutation/utils.py`).
- README and the install snippets in the docs now show `uv add`
  alongside `pip install`.

### CI

- The matrix `test` job now uploads a per-cell coverage artifact
  (`coverage-py<py>-django<dj>` with `coverage.xml` and the `htmlcov/`
  HTML report) for download from the workflow run. The 100% gate is
  unchanged.

## [0.8.1] — 2026-05-01

### Fixed

- `ServiceSpec` and `SelectorSpec` declared `ExtraT` with
  `bound=dict[str, Any]`, which (per PEP 589) rejects `TypedDict`
  subclasses as type arguments — exactly the shape the docs recommend.
  Both bounds are now `Mapping[str, object]`, so user-defined
  `TypedDict` kwargs (`SelectorSpec[QuerySet[Team], TeamScopeKwargs]`,
  etc.) type-check cleanly under `ty` and `mypy`.

## [0.8.0] — 2026-05-01

### Added

- `ServiceSpec.input_data` plus the symmetric three-tier resolver
  (`get_input_data` catch-all on `MutationFlowMixin`, per-action
  `get_<action>_input_data`, and the per-spec callable). Returns a
  mapping merged on top of `request.data` before the
  `input_serializer` validates — purpose-built for lifting URL kwargs
  (parent IDs from nested routes, etc.) into the serializer's input
  for cross-field validation. Server-supplied keys win on conflict.
- `data: InputT` parameter on the lenient `DeleteService` and strict
  `StrictDeleteService` Protocols. Pairs with `ServiceSpec.input_serializer`
  to type a delete-with-payload service end-to-end (e.g. a deletion
  reason). Default is `Ellipsis`, so services that don't read a body
  remain Protocol-compliant — bind `InputT` to the new `NoInput`
  sentinel for clarity.
- `NoKwargs` (empty `TypedDict`) and `NoInput` (sentinel class) under
  `rest_framework_services.types` and re-exported from the package
  root. Saves projects from re-defining the same empty stubs whenever
  a strict service has no extras (`NoKwargs`) or no body (`NoInput`).
- `ServiceAutoSchema` now emits `requestBody` for `DELETE` endpoints
  whose spec carries an `input_serializer`. drf-spectacular's default
  `AutoSchema` suppresses bodies on DELETE; this override keeps the
  generated schema honest for delete-with-payload routes.

### Changed (BREAKING)

- Type-parameter ordering on the delete service Protocols now leads
  with `InputT` to match `StrictCreateService` / `StrictUpdateService`:
  - `DeleteService[InputT, InstanceT, ResultT]` (was
    `DeleteService[InstanceT, ResultT]`).
  - `StrictDeleteService[InputT, InstanceT, ExtraT, ResultT]` (was
    `StrictDeleteService[InstanceT, ExtraT, ResultT]`).

  Migration: at each parameterization site, prepend the input type. For
  services that don't read a body, use the new `NoInput` sentinel:
  `StrictDeleteService[NoInput, Author, NoKwargs, None]`.

## [0.7.0] — 2026-04-30

### Added

- `implements(Protocol[...])` — identity decorator that attaches a strict
  service / selector Protocol shape directly to the decorated function.
  Replaces the `_: StrictCreateService[...] = create_author` shim as the
  recommended way to assert conformance: the assertion lives on the
  function definition (survives renames), reads naturally, and is what
  CI now exercises against the strict Protocols. The shim form continues
  to work.
- `tests/services/strict_drift_fixtures.py` plus a
  `make type-check-strict-fixtures` target — known-bad usages that must
  produce ``ty`` diagnostics, wired into the CI lint job to guard against
  regressions where strict-Protocol drift detection silently breaks.

### Changed (BREAKING)

- Reordered the generic parameters on every strict service / selector
  Protocol so `ExtraT` sits immediately before the result type — the
  parameter list now reads "input, extras, result" and mirrors the call
  shape:
  - `StrictCreateService[InputT, ExtraT, ResultT]`
  - `StrictUpdateService[InputT, InstanceT, ExtraT, ResultT]`
  - `StrictDeleteService[InstanceT, ExtraT, ResultT]`
  - `StrictListSelector[ExtraT, ResultT]`
  - `StrictRetrieveSelector[ExtraT, ResultT]`
  - `StrictOutputSelector[InT, ExtraT, OutT]`

  Migration: swap the last two type arguments at every parameterization
  site. The non-strict Protocols (`CreateService`, `ListSelector`, …)
  are unchanged.
- Dropped the `bound=dict[str, object]` constraint from `ExtraT` on every
  strict Protocol. `TypedDict` subclasses are now accepted as type
  arguments by both `ty` and `mypy` (previously rejected with
  "Type argument … must be a subtype of `dict[str, object]`"). This
  matches the documented intent of the strict Protocols.

## [0.6.1] — 2026-04-29

### Changed

- README and docs index now surface lenient and strict service / selector
  typing examples and link to the typing page. Minor wording fix:
  "opt-out per view" → "opt-out per spec" (atomic lives on
  `ServiceSpec`).

## [0.6.0] — 2026-04-28

### Added

- **OpenAPI / Swagger integration** via an opt-in
  `drf-spectacular` adapter. New optional install extra:
  ``pip install djangorestframework-services[spectacular]``.
  - `rest_framework_services.openapi.enable_openapi()` — wires
    `ServiceAutoSchema` onto every library view class. Call once from
    `AppConfig.ready()`.
  - `ServiceAutoSchema` reads each `ServiceSpec` to derive the request
    body (`input_serializer`), success response (`output_serializer`),
    success status, and a `422` response documenting `ServiceError`.
    `@extend_schema` annotations always win.
  - Bare `@dataclass` `input_serializer` is auto-wrapped in
    `DataclassSerializer` (mirroring the runtime), so dataclasses show
    up as typed schema components instead of bare `object`.
  - `@service_action` handlers stamp `_service_spec` on the wrapped
    handler so the schema generator can recover the spec.
  - `ServiceErrorSerializer` is exported for users who want to reuse the
    422 component shape.
  - New documentation page: `docs/openapi.md`.
- Lenient service / selector Protocols — opt-in shapes for IDE and
  type-checker support. New top-level exports: `CreateService`,
  `UpdateService`, `DeleteService`, `ListSelector`, `RetrieveSelector`,
  `OutputSelector`. Each is parameterized by input / instance / result
  type and keeps a `**kwargs: Any` escape hatch.
- Strict variants — `StrictCreateService`, `StrictUpdateService`,
  `StrictDeleteService`, `StrictListSelector`, `StrictRetrieveSelector`,
  `StrictOutputSelector` — use [PEP 692](https://peps.python.org/pep-0692/)
  `Unpack[TypedDict]` to pin the extras delivered by `ServiceSpec.kwargs`.
  Use these when you want signature drift to fail static checks.
- `ServiceSpec` and `SelectorSpec` are now generic over input / result and
  an optional `TypedDict` of extra kwargs. Unparameterized usage is
  unchanged (`Any` everywhere).
- `ServiceSpec.kwargs` and `SelectorSpec.kwargs` — per-spec callable
  returning extra kwargs for the call. Co-locating the contract with the
  spec replaces `if self.action == ...` branches in `get_service_kwargs`.
- Per-action hooks — `get_<action>_service_kwargs` and
  `get_<action>_selector_kwargs` are now consulted in the kwargs
  resolution chain, so multi-action viewsets can split contracts by
  method name instead of branching on `self.action`.
- `ServiceView` Protocol (in `rest_framework_services.views`) — narrow
  structural shape passed to per-spec kwargs providers, exposing
  `request`, `kwargs`, `action`.
- Fail-fast spec validation. `as_view()` now walks every spec and raises
  `django.core.exceptions.ImproperlyConfigured` on misconfigurations:
  service requires `data` without an `input_serializer`, requires
  `instance` on a create / list action, requires `result` outside an
  `output_selector`, or has a required parameter that no kwargs source
  can supply. `@service_action` validates at decoration time.
- New documentation page: `docs/typing.md`.

### Changed

- **Breaking.** Services and selectors no longer receive `view` in their
  kwargs pool. They are plain business logic; pipe view state through a
  per-spec `kwargs` provider (which receives the view typed as
  `ServiceView`) or `get_<action>_service_kwargs` instead. Migration:
  read whatever you used off `view` and surface it via one of the kwargs
  sources.

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

[Unreleased]: https://github.com/Artui/djangorestframework-services/compare/v0.15.0...HEAD
[0.15.0]: https://github.com/Artui/djangorestframework-services/compare/v0.14.0...v0.15.0
[0.14.0]: https://github.com/Artui/djangorestframework-services/compare/v0.13.0...v0.14.0
[0.13.0]: https://github.com/Artui/djangorestframework-services/compare/v0.12.0...v0.13.0
[0.12.0]: https://github.com/Artui/djangorestframework-services/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/Artui/djangorestframework-services/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/Artui/djangorestframework-services/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/Artui/djangorestframework-services/compare/v0.8.1...v0.9.0
[0.8.1]: https://github.com/Artui/djangorestframework-services/compare/v0.8.0...v0.8.1
[0.8.0]: https://github.com/Artui/djangorestframework-services/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/Artui/djangorestframework-services/compare/v0.6.1...v0.7.0
[0.6.1]: https://github.com/Artui/djangorestframework-services/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/Artui/djangorestframework-services/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/Artui/djangorestframework-services/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/Artui/djangorestframework-services/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/Artui/djangorestframework-services/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Artui/djangorestframework-services/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Artui/djangorestframework-services/releases/tag/v0.1.0
