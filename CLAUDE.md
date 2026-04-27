# djangorestframework-services

A service/selector layer for Django REST Framework that provides precise, controllable side-effect handling for mutating endpoints — without prescribing what a "service" looks like.

Package name on PyPI: `djangorestframework-services`  
Python package: `rest_framework_services`  
Build backend: hatchling (`pyproject.toml`)

---

## Commands

```bash
# Install all dependencies (including dev)
uv sync --all-groups

# Run tests with coverage
uv run pytest --cov=rest_framework_services --cov-fail-under=100

# Lint
uv run ruff check rest_framework_services tests

# Type check
uv run ty check

# Run pre-commit hooks
uv run pre-commit run --all-files
```

---

## Structural Rules

- Each **exported** class or function lives in its **own file**.
- Private helpers used only within the same file: `_name` prefix, kept in that file.
- Non-exported reusable helpers shared across files in a package: `utils.py` inside that package.
- All function/method signatures must have **full type annotations**.
- **No lazy/local imports** — top-level imports only. If a circular import is diagnosed and proven, document it inline with a comment.

---

## Package Layout

```
rest_framework_services/
├── __init__.py                          # re-exports public API
├── py.typed
├── conf.py                              # REST_FRAMEWORK_SERVICES settings dict
│
├── exceptions/
│   ├── __init__.py
│   ├── service_error.py                 # class ServiceError(Exception)
│   └── service_validation_error.py     # class ServiceValidationError(ServiceError)
│
├── types/
│   ├── __init__.py                      # re-exports
│   ├── unset.py                         # UNSET sentinel (and private _Unset class)
│   ├── field_change.py                  # @dataclass FieldChange
│   └── change_result.py                 # @dataclass ChangeResult
│
├── mutations/
│   ├── __init__.py                      # re-exports the helpers only
│   ├── apply_input.py                   # def apply_input(...)
│   ├── create_from_input.py             # def create_from_input(...)
│   ├── update_from_input.py             # def update_from_input(...)
│   ├── acreate_from_input.py            # async def acreate_from_input(...)
│   ├── aupdate_from_input.py            # async def aupdate_from_input(...)
│   └── utils.py                         # non-exported shared helpers
│
├── selectors/
│   ├── __init__.py
│   ├── selector.py                      # Selector Protocol
│   ├── async_selector.py                # AsyncSelector Protocol
│   └── utils.py
│
├── _compat/
│   ├── __init__.py
│   ├── is_async.py                      # def is_async(fn) -> bool
│   ├── run_service.py                   # def run_service(...) — sync + atomic
│   └── arun_service.py                  # async def arun_service(...) — async + atomic
│
├── views/
│   ├── __init__.py
│   ├── utils.py                         # leaf helpers: get_class_attr, resolve_callable_kwargs
│   ├── mutation/
│   │   ├── __init__.py
│   │   ├── service_create_view.py       # class ServiceCreateView
│   │   ├── service_update_view.py       # class ServiceUpdateView
│   │   ├── service_delete_view.py       # class ServiceDeleteView
│   │   ├── mutation_flow_mixin.py       # class MutationFlowMixin
│   │   └── utils.py                     # leaf helpers: validate_input, dispatch_service,
│   │                                    # map_service_error, _execute_mutation
│   └── query/
│       ├── __init__.py
│       ├── selector_list_view.py        # class SelectorListView
│       └── selector_retrieve_view.py    # class SelectorRetrieveView
│
├── viewsets/
│   ├── __init__.py
│   ├── service_viewset.py               # composition of mixins
│   ├── selector_viewset.py              # composition of mixins
│   ├── multi_serializer_mixin.py        # class MultiSerializerMixin
│   ├── selector_list_mixin.py           # class SelectorListMixin
│   ├── selector_retrieve_mixin.py       # class SelectorRetrieveMixin
│   ├── service_create_mixin.py          # class ServiceCreateMixin
│   ├── service_update_mixin.py          # class ServiceUpdateMixin
│   ├── service_destroy_mixin.py         # class ServiceDestroyMixin
│   └── decorators/
│       ├── __init__.py
│       └── service_action.py            # @service_action decorator
│
└── management/
    ├── __init__.py
    ├── commands/
    │   ├── __init__.py
    │   └── startserviceapp.py
    └── templates/
        └── service_app/                 # TemplateCommand template
```

---

## Key Design Decisions

### Services are not prescribed
The library does not define a `Service` base class or enforce a signature. A service is any callable. Views resolve which arguments to pass by **inspecting `inspect.signature(service)`** and drawing from a known pool: `data`, `instance`, `request`, `user`, `view`, plus extras from `get_service_kwargs()`. If the service declares `**kwargs`, everything is passed.

### `mutations/` helpers
The core value-add. Services call these explicitly inside their own bodies — the library never calls them implicitly. All return a `ChangeResult` (defined in `types/`).

- `apply_input` — in-memory only, no `.save()`, returns `ChangeResult`.
- `create_from_input` — creates and saves a new instance.
- `update_from_input` — updates changed fields only (`save(update_fields=[...])` by default).
- `acreate_from_input` / `aupdate_from_input` — async equivalents (Django 4.2+ `asave()`/`aset()`).

All accept:
- `data` — dataclass, plain dict, or any object with `__dict__` / `asdict()`
- `field_map: dict[str, str] | None` — maps input field names to model attribute names
- `exclude_fields: list[str] | None` — fields to skip entirely
- `m2m: dict[str, Any] | None` — M2M assignments applied post-save (`create`/`update` only)

### `types/` package
Framework-agnostic value types shared across the library. Lives outside `mutations/` so future consumers (views, services, user code) aren't coupled to the mutation helpers.

#### `ChangeResult`
```python
@dataclass(frozen=True)
class ChangeResult:
    instance: Model
    created: bool
    changes: tuple[FieldChange, ...]

    def changed_fields(self) -> tuple[str, ...]: ...      # property
    def get_field_change(self, field_name: str) -> FieldChange | None: ...
    def __bool__(self) -> bool: ...   # True iff any change
```

#### `FieldChange`
```python
@dataclass(frozen=True)
class FieldChange:
    field: str
    old: Any   # UNSET for fields populated as part of a create
    new: Any
```

#### `UNSET` sentinel
Distinguishes "field omitted from input" from "field explicitly set to `None`". Critical for partial updates. Lives in `types/unset.py`.

### Transactions
Service calls are wrapped in `transaction.atomic()` by default. Opt out with `atomic = False` on the view class.

### Errors
`ServiceError` and `ServiceValidationError` in `exceptions/` have **no DRF imports**. The view boundary converts them:
- `ServiceValidationError` → DRF `ValidationError` (HTTP 400)
- `ServiceError` → DRF `APIException` (HTTP 422)

### Async
Both sync and async services/selectors are supported. `_compat/is_async.py` detects async callables via `inspect.iscoroutinefunction`. Views dispatch to `run_service` or `arun_service` accordingly. The default views are sync (WSGI-compatible); async services running in a sync view are bridged through `asgiref.sync.async_to_sync`.

### Class-attribute callables
Services and selectors are configured as class attributes (e.g. `service = my_fn`). Plain functions stored as class attributes become bound methods on instance access — to avoid that, all view classes and viewsets retrieve callables via `views.utils.get_class_attr(self, "service")`, which goes through `type(self)` and skips descriptor binding.

### Views and viewsets
The library leans on DRF's existing scaffolding wherever possible. The query side reuses ``ListModelMixin`` / ``RetrieveModelMixin`` end-to-end — selectors are simply overrides for ``get_queryset()`` / ``get_object()``. The mutation side factors the post-validate-dispatch-render flow into a single ``MutationFlowMixin`` that all per-action mixins and standalone views compose; the flow body lives in the private ``_execute_mutation`` helper so ``@service_action`` (a decorator, not a class) can reach it without duplication.

- `ServiceCreateView` / `ServiceUpdateView` / `ServiceDeleteView` — single-purpose ``GenericAPIView`` subclasses (compose ``MutationFlowMixin``); configure ``service``, ``input_dataclass``, ``output_serializer``, ``output_selector``, ``atomic``, ``success_status``.
- `SelectorListView` / `SelectorRetrieveView` — DRF list/retrieve mixins with a ``selector`` override for ``get_queryset()`` / ``get_object()``. Render with the standard DRF ``serializer_class``.
- `ServiceViewSet` — shallow composition of ``ServiceCreateMixin``, ``ServiceUpdateMixin``, ``ServiceDestroyMixin``, ``SelectorListMixin``, ``SelectorRetrieveMixin``, and ``MultiSerializerMixin`` over ``GenericViewSet``. Unconfigured mutation actions raise ``MethodNotAllowed``; unconfigured query actions fall back to DRF defaults.
- `SelectorViewSet` — shallow composition of ``SelectorListMixin``, ``SelectorRetrieveMixin``, and ``MultiSerializerMixin`` over ``GenericViewSet``.
- Per-action mixins (`ServiceCreateMixin`, `ServiceUpdateMixin`, `ServiceDestroyMixin`, `SelectorListMixin`, `SelectorRetrieveMixin`) are exported so users can compose custom viewsets that pick only the actions they need.
- `MutationFlowMixin` is exported as the building block for service-backed action flow on bespoke shapes that don't fit the existing five mixins.
- `MultiSerializerMixin` adds ``serializer_classes: dict[str, type[Serializer]]`` for per-action serializer dispatch (``get_serializer_class()`` consults the map, falls back to DRF's ``serializer_class``).
- `@service_action` — decorator wrapping DRF's ``@action`` and reaching the same ``_execute_mutation`` flow for custom viewset actions.

Selectors are **strict overrides**: ``SelectorListMixin.get_queryset`` always returns the selector's result when the selector is set, regardless of the active action; ``SelectorRetrieveMixin.get_object`` does the same. If you need action-specific behaviour, override ``get_queryset`` / ``get_object`` yourself.

### Selector and output-selector fallbacks
Selectors are **overrides** for vanilla DRF behaviour, not requirements:

- **List action** — when no ``selector`` (or ``list_selector``) is configured, ``get_queryset()`` returns DRF's default (the ``queryset`` class attribute), so filter backends, pagination, and serialization stay vanilla.
- **Retrieve action** — when no ``selector`` (or ``retrieve_selector``) is configured, ``get_object()`` returns DRF's default lookup using ``queryset`` + ``lookup_field``.
- **Mutation `output_selector`** — when missing, the render value is chosen in this order:
  1. The service's return value, if non-``None``.
  2. The in-memory ``instance`` (update/delete views, where it was already pre-fetched).
  3. ``None`` → ``204 No Content``.

A mutation service that mutates in place and returns ``None`` still serializes the post-mutation instance — matching DRF's ``UpdateAPIView`` shape without extra wiring.

### No bespoke query-param parsing
Filtering is not an in-house concept here. Use DRF's ``filter_backends`` (``DjangoFilterBackend``, ``SearchFilter``, ``OrderingFilter``) on the queryset returned by your selector, just as you would with vanilla DRF. The library does not ship a ``filter_dataclass`` mechanism.

---

## Test Conventions

- **100% coverage enforced** — `--cov-fail-under=100` in every CI run.
- Tests live in `tests/`, mirroring the package structure (e.g. `tests/mutations/test_apply_input.py`).
- `tests/testapp/` is a minimal Django app used for integration tests.
- Async tests use `pytest-asyncio`.
- Test files for each exported symbol follow the naming pattern `test_<module_name>.py`.

---

## Compatibility

| Axis | Range |
|---|---|
| Python | 3.10 – 3.14 |
| Django | 4.2, 5.0, 5.1, 5.2, 6.0+ |
| DRF | ≥ 3.14 |

CI runs a full matrix of Python × Django versions with coverage gating.

---

## `startserviceapp`
A Django management command (subclass of ``TemplateCommand``) that scaffolds a service-oriented app layout: ``models/``, ``views/``, ``services/``, ``selectors/``, ``validators/``, ``serializers/``, ``utils/`` are all packages with their own ``__init__.py``; ``apps.py``, ``admin.py``, ``urls.py``, ``migrations/``, ``tests/`` round out the standard surface. The bundled template lives at ``rest_framework_services/management/templates/service_app/`` and is shipped in the wheel. Users add ``"rest_framework_services"`` to ``INSTALLED_APPS`` to make the command discoverable, then run ``python manage.py startserviceapp <name>``.

---

## Examples
A runnable Django project lives in `examples/` — a single-resource invoices app demonstrating CRUD via `ServiceViewSet`, a custom action via `@service_action`, selectors, services, exception mapping, and an end-to-end `APITestCase`. Run with `python manage.py test invoices` from the `examples/` directory.
