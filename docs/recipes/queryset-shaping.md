# Per-spec queryset shaping

Selectors are meant to stay agnostic of any one consumer — a single
`list_authors` selector backs many endpoints. But each endpoint often
needs its own `select_related` / `prefetch_related` / `annotate` shaping
so the response serializer doesn't trigger an N+1.

`SelectorSpec` carries four shaping fields. Three are declarative (apply
the same shaping every request); the fourth is a callable that lets the
shaping depend on the request.

```python
from django.db.models import Count, Prefetch

class PostViewSet(SelectorViewSet):
    queryset = Post.objects.all()
    action_specs = {
        "list": SelectorSpec(
            kind=SelectorKind.LIST,
            selector=list_posts,
            output_serializer=PostSerializer,
            select_related=["author"],
            prefetch_related=["tags", Prefetch("comments", queryset=Comment.objects.active())],
            annotations={"reaction_count": Count("reactions")},
        ),
    }
```

The fields are applied in fixed order — `select_related` →
`prefetch_related` → `annotations` → `extend_queryset` — to the queryset
the selector returns. They run inside `dispatch_selector_for_spec`, so
both list and retrieve flows pick them up.

## Dynamic shaping with `extend_queryset`

When the shaping depends on the incoming request — only prefetch when a
query string opts in, annotate different aggregates per role — set
`extend_queryset` to a callable. It runs *after* the declarative fields,
so it always sees the fully statically-shaped queryset.

```python
def include_relations_from_query_param(queryset, view, request):
    include = set(request.query_params.get("include", "").split(","))
    if "tags" in include:
        queryset = queryset.prefetch_related("tags")
    if "comments" in include:
        queryset = queryset.prefetch_related("comments")
    return queryset

class PostViewSet(SelectorViewSet):
    queryset = Post.objects.all()
    action_specs = {
        "list": SelectorSpec(
            kind=SelectorKind.LIST,
            selector=list_posts,
            output_serializer=PostSerializer,
            select_related=["author"],
            extend_queryset=include_relations_from_query_param,
        ),
    }
```

The callable signature is `(queryset, view, request) -> QuerySet`. `view`
is typed as `ServiceView`; the queryset that goes in must come back out
(returning `None` or a non-QuerySet will trip downstream code with a
confusing error).

`extend_queryset` is **synchronous**: a queryset is a lazy expression
tree and shaping doesn't hit the database. The DB call happens later when
the framework iterates the queryset (list) or `.first()`s it (retrieve).

## Retrieve selectors

For retrieve actions, return a filtered QuerySet from the selector instead
of a single instance. The framework applies shaping then materializes via
`.first()`:

```python
def get_post(*, pk: int):
    return Post.objects.filter(pk=pk)   # QuerySet, not .first()

class PostViewSet(SelectorViewSet):
    queryset = Post.objects.all()
    action_specs = {
        "retrieve": SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=get_post,
            output_serializer=PostSerializer,
            select_related=["author"],
            prefetch_related=["tags"],
        ),
    }
```

Selectors that return an instance directly (the existing pattern) keep
working unchanged, but shaping is a no-op on a materialized instance —
configure either declarative shaping or your own `.select_related(...)`
inside the selector, not both.

## On `ServiceSpec` — shaping the post-mutation re-fetch

The same shaping fields live on the nested
`ServiceSpec.output_selector_spec`. There they apply to the queryset
returned by that spec's `selector` (the re-fetch step of a mutation
flow). The typical pattern: the service creates or updates an instance;
the nested `selector` returns a filtered QuerySet; the nested spec
declares the eager-loading the response serializer needs.

```python
def create_post(*, data, request):
    return Post.objects.create(title=data.title, author=request.user)

def refetch_post(*, result):
    return Post.objects.filter(pk=result.pk)   # QuerySet, not .first()

class CreatePostView(ServiceCreateView):
    spec = ServiceSpec(
        service=create_post,
        input_serializer=CreatePostInput,
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=refetch_post,
            output_serializer=PostSerializer,
            select_related=["author"],
            prefetch_related=["tags"],
        ),
    )
```

After shaping, the framework `.first()`s the QuerySet to a single
instance before the output serializer runs. Re-fetch selectors that
return a materialized instance directly (no
`.filter().select_related(...)`) keep working — shaping is a no-op in
that case.

The shaping fields require `selector` to be set on the nested spec.
Configuring them on a nested spec with no selector raises
`ImproperlyConfigured` at `as_view()` time, mirroring the standalone
`SelectorSpec`/`selector` rule.

## Where shaping does *not* apply

- **The nested `SelectorSpec.selector` is `None`** — the spec has
  nothing to shape. Configuring shaping then raises
  `ImproperlyConfigured` at `as_view()` time to surface the
  misconfiguration loudly.
- **The shaped callable returns a non-QuerySet** (a list, dict, instance,
  generator). Shaping requires a queryset to chain `.select_related(...)` /
  `.prefetch_related(...)` / `.annotate(...)`. The dispatcher raises
  `ImproperlyConfigured` at request time when shaping is set but the
  callable returned something other than a queryset. Drop the shaping
  fields or have the callable return a QuerySet.
- **Filter backends and pagination** still run as usual on top of the
  shaped queryset.

## List selectors aren't required to return a QuerySet

The shaping fields are the *only* part of the pipeline that needs a
QuerySet. A `LIST` selector can return **any iterable** — a plain `list`,
a `tuple`, the output of a `@dataclass`-building comprehension, results
stitched together from an external API — and it is serialized as-is:

```python
def recent_activity(*, user):
    # not a QuerySet — a hand-built list of dataclasses
    return [ActivityItem.from_event(e) for e in fetch_events(user)]

class ActivityView(SelectorListView):
    spec = SelectorSpec(
        kind=SelectorKind.LIST,
        selector=recent_activity,
        output_serializer=ActivityItemSerializer,
    )
```

Two caveats, both inherent rather than framework-imposed:

- **Don't combine a non-QuerySet return with the shaping fields.**
  `select_related` / `prefetch_related` / `annotations` / `extend_queryset`
  are QuerySet operations; setting them while returning a list raises
  `ImproperlyConfigured` at request time (see above).
- **Pagination needs a sized, sliceable sequence.** A `list`/`tuple`
  paginates fine (Django's `Paginator` only needs `len()` + slicing). A
  lazy **generator** works when pagination is *off*, but the paginators
  that slice or count (`PageNumberPagination`, `LimitOffsetPagination`,
  `CursorPagination`) will consume or reject it — materialize to a `list`
  first if you need pagination.
