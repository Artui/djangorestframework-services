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

The bare callable — not a `ServiceSpec` — goes into `service_specs` for
read-side actions:

```python
service_specs = {
    "list": list_authors,
    "retrieve": get_author,
}
```

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
| `SelectorListView` | `GET` | uses `selector` (or `queryset`) for list |
| `SelectorRetrieveView` | `GET` | uses `selector` (or `queryset` + `lookup_field`) for retrieve |

Mutation views are configured by setting `spec` to a `ServiceSpec`.
Selector views configure `selector` and DRF's standard
`serializer_class`.

## Viewsets

`ServiceViewSet` is a router-compatible viewset composed of per-action
mixins. A single `service_specs` mapping wires everything:

```python
class AuthorViewSet(ServiceViewSet):
    queryset = Author.objects.all()
    serializer_classes = {
        "list": AuthorListItemSerializer,
        "retrieve": AuthorDetailSerializer,
    }
    service_specs = {
        "list": list_authors,
        "retrieve": get_author,
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

- Read-side actions take a bare callable (the selector).
- Write-side actions take a `ServiceSpec`.
- Absent (or non-`ServiceSpec`) entries on a write action make that
  action return `405 Method Not Allowed`.

`SelectorViewSet` is a pre-built read-only composition (list +
retrieve only).

Per-action mixins (`ServiceCreateMixin`, `ServiceUpdateMixin`,
`ServiceDestroyMixin`, `SelectorListMixin`, `SelectorRetrieveMixin`,
`MultiSerializerMixin`) are exported so you can compose only the actions
you need — see the [compose-viewset recipe](recipes/compose-viewset.md).

## `MultiSerializerMixin`

Per-action serializer dispatch via a single mapping:

```python
serializer_classes = {
    "list": ListSerializer,
    "retrieve": DetailSerializer,
    "my_custom_action": CustomSerializer,
}
```

`get_serializer_class()` consults the map first, falls back to
`serializer_class`.

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
