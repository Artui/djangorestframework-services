# Concepts

Three building blocks. Everything else composes them.

| Block | What it is | Where it lives |
|---|---|---|
| **Service** | A plain callable that performs a mutation | Your code |
| **Selector** | A plain callable that returns data to read | Your code |
| **View / Viewset** | DRF view that wires a service or selector to an HTTP method | This library |

## Services

A service is any callable. The library does not define a `Service` base
class. It dispatches the callable, hands it the kwargs it asks for, and
maps any framework-agnostic exception it raises to a DRF response.

```python
def create_author(*, data, user):
    return Author.objects.create(name=data.name, created_by=user)
```

A service can return:

- the freshly mutated model instance (DRF's typical pattern),
- the matching output dataclass (when the API surface diverges from the
  model),
- `None` for update / delete flows — the in-memory instance is rendered
  instead, matching DRF's `UpdateAPIView` shape without you wiring it up.

## Selectors

A selector is a callable used by read-side flows. It overrides
`get_queryset()` for list and `get_object()` for retrieve. Filter
backends, pagination, and serialization stay vanilla DRF.

```python
def list_authors(*, request):
    return Author.objects.filter(account=request.user.account)
```

Selectors go into `action_specs` wrapped in a `SelectorSpec` whose
`kind` declares whether the action returns many objects or a single one:

```python
action_specs = {
    "list": SelectorSpec(kind=SelectorKind.LIST, selector=list_authors),
    "retrieve": SelectorSpec(kind=SelectorKind.RETRIEVE, selector=get_author),
}
```

## `SelectorSpec`

`SelectorSpec` is a frozen dataclass (keyword-only fields) bundling
everything a read action needs:

```python
@dataclass(frozen=True, kw_only=True)
class SelectorSpec(Generic[ResultT, ExtraT]):
    kind: SelectorKind                              # required
    selector: Callable[..., ResultT] | None = None
    output_serializer: type[Serializer] | None = None
    kwargs: Callable[[ServiceView, Request], ExtraT] | None = None
    permission_classes: Sequence[type[BasePermission]] | None = None
    output_serializer_context: Callable[[ServiceView, Request], Mapping[str, Any]] | None = None
    select_related: Sequence[str] | None = None
    prefetch_related: Sequence[str | Prefetch] | None = None
    annotations: Mapping[str, Any] | None = None
    extend_queryset: Callable[[QuerySet, ServiceView, Request], QuerySet] | None = None
```

- **`kind`** — required `SelectorKind` discriminator (`LIST` vs
  `RETRIEVE`). Drives dispatch: `RETRIEVE` materializes a QuerySet via
  `.first()` and raises `NotFound` on `None`, `LIST` returns the
  (optionally shaped) selector result. Also drives the fail-fast
  cross-check that the spec is mounted on a compatible view — a `LIST`
  spec on `SelectorRetrieveView` (or `action_specs["retrieve"]`) raises
  `ImproperlyConfigured` at `as_view()` time. Making the kind explicit
  also lets a spec be reused outside a request (management command,
  cron job, non-DRF caller) without the semantics living implicitly in
  the call site.
- **`selector`** — the callable invoked by `get_queryset()` (list) or
  `get_object()` (retrieve). `None` means "use the configured `queryset`
  / default DRF behaviour".
- **`output_serializer`** — a DRF `Serializer` subclass used by
  `get_serializer_class()` for this action. `None` falls back to DRF's
  standard `serializer_class`.
- **`kwargs`** — callable returning extra kwargs to merge into the pool
  the selector receives. The most-specific level of the kwargs
  resolution chain; co-located with the selector it feeds. Receives
  the view (typed as the narrow `ServiceView` Protocol) and the
  current `Request`. See the [extra-kwargs recipe](recipes/extra-kwargs.md).
- **`permission_classes`** — overrides the view's class-level
  `permission_classes` for the action the spec backs. `None` (the default)
  inherits; `[]` means "no permissions" explicitly. Ignored when the
  spec is nested under `ServiceSpec.output_selector_spec` (the
  surrounding mutation's permissions apply). See the
  [permissions recipe](recipes/permissions.md).
- **`output_serializer_context`** — callable returning extra keys for
  the response serializer's `context=` dict. Sits at the most-specific
  layer of the [serializer-context resolution chain](recipes/serializer-context.md).
- **`select_related`** / **`prefetch_related`** / **`annotations`** /
  **`extend_queryset`** — declarative + dynamic queryset shaping applied
  to the selector's return value inside `dispatch_selector_for_spec`. See
  the [queryset-shaping recipe](recipes/queryset-shaping.md).

Generic parameters `ResultT` / `ExtraT` default to `Any`, so
`SelectorSpec(kind=..., selector=fn)` keeps working unparameterized.

## `ServiceSpec`

`ServiceSpec` is a frozen dataclass bundling everything a write action
needs. The entire output pipeline (response serializer, optional
post-mutation re-fetch, queryset shaping) lives in a single nested
`output_selector_spec: SelectorSpec | None`:

```python
@dataclass(frozen=True)
class ServiceSpec(Generic[InputT, ResultT, ExtraT]):
    service: Callable[..., ResultT]
    atomic: bool = True
    success_status: int | None = None
    input_serializer: type | None = None
    input_data: Callable[[ServiceView, Request], Mapping[str, Any]] | None = None
    input_serializer_context: Callable[[ServiceView, Request], Mapping[str, Any]] | None = None
    output_selector_spec: SelectorSpec[Any, Any] | None = None
    kwargs: Callable[[ServiceView, Request], ExtraT] | None = None
    permission_classes: Sequence[type[BasePermission]] | None = None
```

- **`service`** — the callable to invoke.
- **`atomic`** — wrap the service call in `transaction.atomic()`
  (defaults `True`).
- **`success_status`** — override the HTTP status (defaults to
  `201` for create, `200` for update, `204` for delete).
- **`input_serializer`** — a DRF `Serializer` subclass, a bare
  `@dataclass` (auto-wrapped in `DataclassSerializer`), or `None` for
  side-effect-only services.
- **`input_data`** — callable returning a mapping merged on top of
  `request.data` *before* the `input_serializer` validates it. Useful
  for lifting URL kwargs (e.g. parent IDs from nested routes) into
  fields the serializer can cross-validate. Server-provided keys win
  on conflict.
- **`input_serializer_context`** — callable returning extra keys for
  the *input* serializer's `context=` dict. Sits at the most-specific
  layer of the [serializer-context resolution chain](recipes/serializer-context.md).
- **`output_selector_spec`** — nested `SelectorSpec`
  (`kind=SelectorKind.RETRIEVE`) carrying the response serializer, the
  optional re-fetch `selector`, the output `output_serializer_context`
  hook, and the queryset-shaping fields. `None` (the default) renders
  the service's return value directly. The nested spec's
  `permission_classes` and `kwargs` are ignored — the surrounding
  mutation's permissions and kwargs chain apply.
- **`kwargs`** — callable returning extra kwargs to merge into the pool
  the service receives. The most-specific level of the kwargs
  resolution chain; co-located with the service it feeds.
- **`permission_classes`** — overrides the view's class-level
  `permission_classes` for the action the spec backs. `None` (the default)
  inherits; `[]` means "no permissions" explicitly. See the
  [permissions recipe](recipes/permissions.md).

Generic parameters `InputT` / `ResultT` / `ExtraT` default to `Any`, so
`ServiceSpec(service=fn)` keeps working unparameterized.

## Dispatch

The view inspects the service / selector signature with
`inspect.signature` and passes only the arguments the callable
declares from a known pool:

| Kwarg | Source |
|---|---|
| `data` | `serializer.validated_data` (a dataclass instance for `DataclassSerializer`, a `dict` for plain `Serializer` / `ModelSerializer`) |
| `instance` | `self.get_object()` (update / destroy only) |
| `request` | `self.request` |
| `user` | `self.request.user` |
| URL kwargs | `self.kwargs` (list / retrieve selectors only — `pk`, parent IDs from nested routes, etc.) |
| extras | `self.get_service_kwargs()` / `self.get_selector_kwargs()`, plus per-action and per-spec hooks |

`view` is intentionally not in the pool — services and selectors are
plain business logic and shouldn't reach back into the calling view. When
a callable needs view state (URL kwargs, action name, etc.), pipe it
through `ServiceSpec.kwargs` / `SelectorSpec.kwargs` (which receive a
narrow `ServiceView`) or `get_<action>_*_kwargs` instead. See
[Pass extra kwargs](recipes/extra-kwargs.md).

If the callable declares `**kwargs`, the entire pool is forwarded. The
implementation lives in
[`rest_framework_services.views.utils.resolve_callable_kwargs`](reference/views.md#kwarg-resolution).

```python
def create_author(*, data, user):       # the view passes only data + user
    return Author.objects.create(name=data.name, created_by=user)

def list_authors(*, request):           # request is in the pool
    return Author.objects.filter(account=request.user.account)
```

This matters because:

- **You don't have to declare a fixed signature.** Add a kwarg when you
  need it; remove it when you don't.
- **Optional kwargs cost nothing.** A service that doesn't declare
  `request` simply doesn't get it.
- **Custom kwargs are first-class.** Override `get_service_kwargs()` /
  `get_selector_kwargs()` to add anything else (a tenant, a feature
  flag, a clock for tests). See the
  [extra-kwargs recipe](recipes/extra-kwargs.md).

## Views

| Class | Method | Purpose |
|---|---|---|
| `ServiceCreateView` | `POST` | runs `service` to create |
| `ServiceUpdateView` | `PUT` / `PATCH` | runs `service` to update; instance from `get_object()` |
| `ServiceDeleteView` | `DELETE` | runs `service` to delete |
| `SelectorListView` | `GET` | uses `spec.selector` (or `queryset`) for list |
| `SelectorRetrieveView` | `GET` | uses `spec.selector` (or `queryset` + `lookup_field`) for retrieve |

Mutation views are configured by setting `spec` to a `ServiceSpec`.
Selector views are configured by setting `spec` to a `SelectorSpec`.

## Viewsets

`ServiceViewSet` is a router-compatible viewset composed of per-action
mixins. A single `action_specs` mapping wires everything:

```python
_author_detail = SelectorSpec(
    kind=SelectorKind.RETRIEVE,
    output_serializer=AuthorDetailSerializer,
)


class AuthorViewSet(ServiceViewSet):
    queryset = Author.objects.all()
    action_specs = {
        "list": SelectorSpec(
            kind=SelectorKind.LIST,
            selector=list_authors,
            output_serializer=AuthorListItemSerializer,
        ),
        "retrieve": SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=get_author,
            output_serializer=AuthorDetailSerializer,
        ),
        "create": ServiceSpec(
            service=create_author,
            input_serializer=CreateAuthorInput,
            output_selector_spec=_author_detail,
        ),
        "update": ServiceSpec(
            service=update_author,
            input_serializer=UpdateAuthorInput,
            output_selector_spec=_author_detail,
        ),
        "destroy": ServiceSpec(service=delete_author),
    }
```

- Read-side actions take a `SelectorSpec`.
- Write-side actions take a `ServiceSpec`.
- Absent entries on a write action make that action return
  `405 Method Not Allowed`.
- A wrong-type entry (e.g. `SelectorSpec` on `create`) raises
  `ImproperlyConfigured` at request time.

`SelectorViewSet` is a pre-built read-only composition (list +
retrieve only).

Per-action mixins (`ServiceCreateMixin`, `ServiceUpdateMixin`,
`ServiceDestroyMixin`, `SelectorListMixin`, `SelectorRetrieveMixin`) are
exported so you can compose only the actions you need — see the
[compose-viewset recipe](recipes/compose-viewset.md).

## `ActionSerializerResolver`

Resolves `get_serializer_class()` from the active action's `action_specs`
entry:

```python
spec = action_specs.get(self.action)
if isinstance(spec, SelectorSpec) and spec.output_serializer:
    return spec.output_serializer
if isinstance(spec, ServiceSpec) and spec.output_selector_spec and \
        spec.output_selector_spec.output_serializer:
    return spec.output_selector_spec.output_serializer
# falls back to serializer_class
```

Works for both `SelectorSpec` (reads `spec.output_serializer`) and
`ServiceSpec` (reads `spec.output_selector_spec.output_serializer`)
entries. Falls back to DRF's standard `serializer_class` when the
action has no spec or no response serializer is set. Already included
in `ServiceViewSet` and `SelectorViewSet`; add it to any custom
composition that needs per-action serializers.

## `@service_action`

Custom viewset actions wrapped in the same plumbing as the standard
mutation flow. See the [service-action recipe](recipes/service-action.md).

## What this library deliberately does *not* do

- It does not define a `Service` base class. A service is a function.
- It does not invent a queryset filtering DSL. Use DRF's
  `filter_backends`.
- It does not own the input format. Use any DRF `Serializer` (including
  `ModelSerializer`) or a bare `@dataclass`.
- It does not decide your project layout. The `startserviceapp`
  scaffold is a starting point, not a contract.
