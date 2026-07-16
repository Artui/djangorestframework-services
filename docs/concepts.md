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
    allow_none: bool = False
    output_serializer: type[Serializer] | None = None
    kwargs: Callable[..., ExtraT] | None = None
    permission_classes: Sequence[type[BasePermission]] | None = None
    output_serializer_context: Callable[..., Mapping[str, Any]] | None = None
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
- **`allow_none`** — `RETRIEVE`-only knob for the `None` / missing-object
  case. `False` (the default) raises `NotFound`. `True` expresses a
  nullable-resource contract: the retrieve view / viewset mixin renders
  `200` with a JSON `null` body, skipping the output serializer — for
  singleton-style resources that legitimately may not exist yet. Ignored
  on nested specs: `output_selector_spec` keeps its authoritative-`None`
  → 204 contract, and `instance_selector_spec` always 404s (a mutation
  against a missing row is not a nullable read).
- **`output_serializer`** — a DRF `Serializer` subclass used by
  `get_serializer_class()` for this action. `None` falls back to DRF's
  standard `serializer_class`.
- **`kwargs`** — callable returning extra kwargs to merge into the pool
  the selector receives. The most-specific level of the kwargs
  resolution chain; co-located with the selector it feeds. Invoked through
  the framework's keyword pool — it declares any subset of `view` /
  `request` (or `**kwargs`) and receives only what it asks for. See the
  [extra-kwargs recipe](recipes/extra-kwargs.md).
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
    success_status: int | Callable[..., int] | None = None
    partial: bool | None = None
    input_serializer: type | None = None
    input_data: Callable[..., Mapping[str, Any]] | None = None
    input_serializer_context: Callable[..., Mapping[str, Any]] | None = None
    instance_selector_spec: SelectorSpec[Any, Any] | None = None
    output_selector_spec: SelectorSpec[Any, Any] | None = None
    kwargs: Callable[..., ExtraT] | None = None
    permission_classes: Sequence[type[BasePermission]] | None = None
    response_finalizer: Callable[..., Response | None] | None = None
```

- **`service`** — the callable to invoke.
- **`atomic`** — wrap the service call in `transaction.atomic()`
  (defaults `True`).
- **`success_status`** — override the HTTP status (defaults to
  `201` for create, `200` for update, `204` for delete). May also be a
  **callable** resolved through the keyword pool (`result` / `instance` /
  `request` / `view`) returning the status — the callable keys on the
  *service's* return value, so an upsert can answer `201` when it created a
  row and `200` when it found one:

  ```python
  def _upsert(*, data):
      author, created = Author.objects.get_or_create(name=data.name)
      return UpsertResult(author=author, created=created)

  ServiceSpec(
      service=_upsert,
      input_serializer=AuthorIn,
      success_status=lambda *, result: 201 if result.created else 200,
      output_selector_spec=SelectorSpec(
          kind=SelectorKind.RETRIEVE,
          selector=lambda *, result: Author.objects.filter(pk=result.author.pk),
          output_serializer=AuthorSerializer,
      ),
  )
  ```

  OpenAPI documents the action default for the callable case (it can't be
  resolved statically).
- **`partial`** — override the transport-derived partial-validation flag.
  `None` (the default) inherits what the verb implies (`False` for
  PUT/POST, `True` for PATCH); `True`/`False` forces it. Applied once at
  the central dispatch point, so it works uniformly across viewset
  mixins, standalone views, `@service_action` — and create dispatch.
  See [PATCH that validates like PUT](#patch-that-validates-like-put).
- **`many`** — validate the request body as a *list* and hand the
  validated list to the service (which loops itself); the result list is
  rendered the same way. The `serializer(many=True)` sibling of `partial`,
  mutually exclusive with `collection_selector_spec`. See
  [Bulk mutations](#bulk-mutations).
- **`input_serializer`** — a DRF `Serializer` subclass, a bare
  `@dataclass` (auto-wrapped in `DataclassSerializer`), or `None` for
  side-effect-only services.
- **`input_data`** — callable returning a mapping merged on top of
  `request.data` *before* the `input_serializer` validates it. Useful
  for lifting URL kwargs (e.g. parent IDs from nested routes) into
  fields the serializer can cross-validate. Server-provided keys win
  on conflict. May additionally declare `instance` as a keyword
  parameter to receive the resolved mutation target (`None` on create)
  — passed only when declared — so pre-validation input mutation that
  depends on the current row has a home.
- **`instance_selector_spec`** — nested `SelectorSpec`
  (`kind=SelectorKind.RETRIEVE`) resolving the instance an update /
  destroy / detail action targets, embedding the lookup in the spec
  instead of the view's `queryset` / `get_object()` chain. The selector
  pool is `{request, user}` + the URL kwargs + the selector extras
  chain, so `selector=lambda *, pk: Project.objects.filter(pk=pk)`
  resolves the row from the route. Resolution happens *before* input
  validation; the resolved instance feeds the input serializer
  (DRF-style `serializer(instance, data=..., partial=...)`), the
  service pool (`instance`), and object-level permission checks
  (`check_object_permissions`). `None` / missing → `404`. Queryset
  shaping applies; the nested spec's `permission_classes`,
  `output_serializer`, and `output_serializer_context` are ignored.
  `None` (the default) keeps the `get_object()` chain. See
  [Standalone update without a queryset](#standalone-update-without-a-queryset).
- **`collection_selector_spec`** — the bulk twin of
  `instance_selector_spec`: a `kind=SelectorKind.LIST` nested spec that
  resolves a *set* (scoped by the selector + `filter_set`) and seeds it
  into the service pool as `collection` for an instance-less bulk delete /
  update. Mutually exclusive with `many`. See [Bulk mutations](#bulk-mutations).
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
- **`response_finalizer`** — a post-serialization HTTP hook for cookie /
  header side effects (or swapping the response). Runs on the **2xx path
  only**, after the output serializer builds the `Response` and before it is
  returned; error paths bypass it. Resolved through the keyword pool
  (`response` / `result` / `request` / `view` / `instance` / `data`), it
  returns a `Response` to replace the built one, or `None` to keep it:

  ```python
  def _set_session_cookie(*, response, result):
      response.set_cookie("session", result.token, httponly=True)
      return response

  ServiceSpec(service=_login, input_serializer=LoginIn,
              response_finalizer=_set_session_cookie)
  ```

  `result` is the *service's* return value, so services stay DRF-free —
  return domain flags on the result DTO and let the finalizer translate them
  into transport effects. **HTTP-only**: skipped on the transport-neutral
  path (`dispatch_spec` / `call_service` / MCP).

Generic parameters `InputT` / `ResultT` / `ExtraT` default to `Any`, so
`ServiceSpec(service=fn)` keeps working unparameterized.

### PATCH that validates like PUT

`partial` composes with the `"partial_update"` action key (which resolves
first and falls back to `"update"` — uniformly at dispatch, permission
resolution, and serializer resolution):

```python
action_specs = {
    "partial_update": ServiceSpec(
        service=set_project_status,
        input_serializer=ProjectStatusInput,  # one required field
        partial=False,                        # required stays required under PATCH
    ),
}
```

Defining *only* `"partial_update"` gives a PATCH-only update endpoint —
PUT returns `405`. On the standalone `ServiceUpdateView` both verbs share
one spec, so a forced `partial` applies to PUT *and* PATCH; set
`http_method_names = ["patch"]` for the PATCH-only standalone equivalent.

### Standalone update without a queryset

With `instance_selector_spec`, a standalone mutation view needs no
`queryset` / `lookup_field` — the spec is self-contained:

```python
class SetProjectStatusView(ServiceUpdateView):
    spec = ServiceSpec(
        service=set_project_status,
        input_serializer=ProjectStatusInput,
        instance_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=lambda *, pk: Project.objects.filter(pk=pk),
        ),
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, output_serializer=ProjectSerializer
        ),
    )
```

The same field works on viewset `update` / `partial_update` / `destroy`
entries and `@service_action(detail=True)` actions, where it takes
precedence over an `action_specs["retrieve"]` selector and the DRF
default lookup.

### Bulk mutations

Two mutually-exclusive `ServiceSpec` fields cover what a single-instance spec
can't. Both run through the same validate → dispatch → render flow — and the
same transport-neutral `dispatch_spec` — so the rules hold on and off HTTP.

**`many=True`** — a list body in, a list out. The `input_serializer` validates
the payload as a list and the service receives that validated list, looping
itself:

```python
class BulkCreateBooksView(ServiceCreateView):
    spec = ServiceSpec(
        service=bulk_create_books,          # (*, data: list[BookIn]) -> list[Book]
        input_serializer=BookIn,
        many=True,
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, output_serializer=BookSerializer
        ),
    )
```

**`collection_selector_spec`** — operate on a filtered set with no `pk` in the
URL. Its `kind=SelectorKind.LIST` selector (scoped by `filter_set`) resolves the
target set and seeds it into the service as `collection` for a bulk delete /
update; an empty set is a harmless no-op:

```python
class BulkDeleteBooksView(ServiceDeleteView):
    spec = ServiceSpec(
        service=delete_collection(Book),    # collection.delete()
        collection_selector_spec=SelectorSpec(
            kind=SelectorKind.LIST, selector=published_books, filter_set=BookFilterSet
        ),
    )
```

To render the affected set instead of a summary, give that collection target an
`output_selector_spec` whose `kind` is `SelectorKind.LIST` — it re-fetches and
renders the rows as a list. Full walkthrough in the
[bulk & collection mutations recipe](recipes/bulk-mutations.md).

## Dispatch

The view inspects the service / selector signature with
`inspect.signature` and passes only the arguments the callable
declares from a known pool:

| Kwarg | Source |
|---|---|
| `data` | `serializer.validated_data` (a dataclass instance for `DataclassSerializer`, a `dict` for plain `Serializer` / `ModelSerializer`) |
| `serializer` | the bound, validated input serializer (update flows construct it instance-aware) — declare it to call `.save()` from the service when persistence lives on the serializer (nested-write patterns) |
| `instance` | `spec.instance_selector_spec` when set, else `self.get_object()` (update / destroy only) |
| `request` | `self.request` |
| `user` | `self.request.user` |
| URL kwargs | `self.kwargs` (list / retrieve selectors and `instance_selector_spec` lookups — `pk`, parent IDs from nested routes, etc.) |
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

## Result rendering

What a mutation responds with is decided by three inputs: what the
service returns, whether the spec carries an output pipeline, and
whether `success_status` is set explicitly. The full matrix:

| Service returns | `output_selector_spec` | Response |
|---|---|---|
| a value | with `output_serializer` (no selector) | serialized value at `success_status` (default 200/201) |
| a value | with `selector` | selector re-fetches (shaping applied, QuerySet materialized via `.first()`); result serialized at `success_status` |
| a value | `None` | the raw value at `success_status` — only useful for JSON-native returns (dicts, lists) |
| `None` | with `output_serializer` (no selector) | update flows render the *in-memory instance* through the serializer at `success_status` (DRF `UpdateAPIView` shape); destroy never resurrects the deleted instance — empty body |
| `None` | with `selector` that returns `None` | the selector's `None` is authoritative → empty body at `204` (always, even with a custom `success_status`) |
| `None` | `None` | empty body at the explicitly-set `spec.success_status`, else `204` |

Two consequences worth knowing:

- A destroy (or any no-output mutation) can carry a custom
  `success_status` and still send an empty body.
- **Stale fetch-time annotations:** when the service mutates in place
  and returns `None`, the update fallback renders the instance *as it
  was looked up* — annotations and shaping from the instance lookup
  reflect **pre-mutation** state. The supported pattern for "respond
  with the post-mutation truth" is an `output_selector_spec` re-fetch:

```python
# Before — counter annotated at lookup time is stale in the response:
ServiceSpec(service=add_item, instance_selector_spec=_with_item_count)

# After — re-fetch renders post-mutation state:
ServiceSpec(
    service=add_item,
    instance_selector_spec=_by_pk,
    output_selector_spec=SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=lambda *, result: Checklist.objects.filter(pk=result.pk),
        annotations={"item_count": Count("items")},
        output_serializer=ChecklistSerializer,
    ),
)
```

## Views

| Class | Method | Purpose |
|---|---|---|
| `ServiceCreateView` | `POST` | runs `service` to create |
| `ServiceUpdateView` | `PUT` / `PATCH` | runs `service` to update; instance from `spec.instance_selector_spec` or `get_object()` |
| `ServiceDeleteView` | `DELETE` | runs `service` to delete; instance from `spec.instance_selector_spec` or `get_object()` |
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
- `PATCH` resolves `action_specs["partial_update"]` first and falls back
  to `"update"` — the same chain applies at dispatch, permission
  resolution, and serializer resolution, so an `"update"`-keyed spec's
  `permission_classes` guard PATCH too. A dedicated `"partial_update"`
  entry can carry its own serializer / service / `partial` override.
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
entry (following the same `"partial_update"` → `"update"` fallback chain
as dispatch):

```python
spec = resolve_action_spec_entry(action_specs, self.action)
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
  `filter_backends` for list endpoints; for a *retrieve* selector (which
  DRF's `filter_backends` never reach) or a transport-neutral declaration,
  point `SelectorSpec.filter_set` at a `django-filter` `FilterSet` — see
  [filter a selector with `filter_set`](recipes/selector-filtering.md).
- It does not own the input format. Use any DRF `Serializer` (including
  `ModelSerializer`) or a bare `@dataclass`.
- It does not decide your project layout. The `startserviceapp`
  scaffold is a starting point, not a contract.
- It does not insist every endpoint be a spec. Constant / no-logic
  endpoints (an enum map, a static config payload) are fine as plain
  DRF views — there is no service or selector to declare, so wrapping
  them buys nothing.
