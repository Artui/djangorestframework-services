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

Selectors go into `action_specs` wrapped in a `SelectorSpec`:

```python
action_specs = {
    "list": SelectorSpec(selector=list_authors),
    "retrieve": SelectorSpec(selector=get_author),
}
```

## `SelectorSpec`

`SelectorSpec` is a frozen dataclass bundling everything a read action
needs:

```python
@dataclass(frozen=True)
class SelectorSpec:
    selector: Callable | None = None
    output_serializer: type[Serializer] | None = None
```

- **`selector`** — the callable invoked by `get_queryset()` (list) or
  `get_object()` (retrieve). `None` means "use the configured `queryset`
  / default DRF behaviour".
- **`output_serializer`** — a DRF `Serializer` subclass used by
  `get_serializer_class()` for this action. `None` falls back to DRF's
  standard `serializer_class`.

## `ServiceSpec`

`ServiceSpec` is a frozen dataclass bundling everything a write action
needs:

```python
@dataclass(frozen=True)
class ServiceSpec:
    service: Callable
    input_serializer: type[Serializer] | type | None = None
    output_serializer: type[Serializer] | None = None
    output_selector: Callable | None = None
    atomic: bool = True
    success_status: int | None = None
```

- **`service`** — the callable to invoke.
- **`input_serializer`** — a DRF `Serializer` subclass, a bare
  `@dataclass` (auto-wrapped in `DataclassSerializer`), or `None` for
  side-effect-only services.
- **`output_serializer`** — a DRF `Serializer` subclass to render the
  result; omit to return whatever the service returned (already
  JSON-serialisable).
- **`output_selector`** — a callable run on the service's return value
  before output rendering. Useful when the service returns an id and the
  output should be a fully-shaped read model.
- **`atomic`** — wrap the service call in `transaction.atomic()`
  (defaults `True`).
- **`success_status`** — override the HTTP status (defaults to
  `201` for create, `200` for update, `204` for delete).

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
| `view` | `self` |
| extras | `self.get_service_kwargs()` / `self.get_selector_kwargs()` |

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
class AuthorViewSet(ServiceViewSet):
    queryset = Author.objects.all()
    action_specs = {
        "list": SelectorSpec(
            selector=list_authors,
            output_serializer=AuthorListItemSerializer,
        ),
        "retrieve": SelectorSpec(
            selector=get_author,
            output_serializer=AuthorDetailSerializer,
        ),
        "create": ServiceSpec(
            service=create_author,
            input_serializer=CreateAuthorInput,
            output_serializer=AuthorDetailSerializer,
        ),
        "update": ServiceSpec(
            service=update_author,
            input_serializer=UpdateAuthorInput,
            output_serializer=AuthorDetailSerializer,
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
if spec has output_serializer:
    return spec.output_serializer
# falls back to serializer_class
```

Works for both `SelectorSpec` and `ServiceSpec` entries. Falls back to
DRF's standard `serializer_class` when the action has no spec or no
`output_serializer`. Already included in `ServiceViewSet` and
`SelectorViewSet`; add it to any custom composition that needs per-action
serializers.

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
