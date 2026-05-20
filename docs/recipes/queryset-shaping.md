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

## Where shaping does *not* apply

- **`spec.selector is None`** — the spec opts out of selector dispatch
  and `dispatch_selector_for_spec` is never called. Configuring shaping
  on a selector-less spec raises `ImproperlyConfigured` at `as_view()`
  time to surface the misconfiguration loudly.
- **Selector returns a non-QuerySet** (a list, dict, instance, generator).
  Shaping requires a queryset to chain `.select_related(...)` /
  `.prefetch_related(...)` / `.annotate(...)`. The dispatcher raises
  `ImproperlyConfigured` at request time when shaping is set but the
  selector returned something other than a queryset. Drop the shaping
  fields or have the selector return a QuerySet.
- **Filter backends and pagination** still run as usual on top of the
  shaped queryset.
