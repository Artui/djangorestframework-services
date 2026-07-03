# Filtering a selector with `filter_set`

A list (or retrieve) selector returns a queryset; *which fields are filterable,
with which lookups* is a separate, declarative concern. `SelectorSpec.filter_set`
holds a [`django-filter`](https://django-filter.readthedocs.io/) `FilterSet` and
applies it to the selector's queryset, reading the values off
`request.query_params`.

```text
selector() -> QuerySet
   → select_related / prefetch_related / annotations / extend_queryset
   → filter_set(data=request.query_params, queryset=qs).qs
   → (retrieve) .first()
   → output_serializer
```

`filter_set` is the fifth [queryset-shaping field](queryset-shaping.md); it runs
**last**, after the eager-loading fields, so filtering composes with them and (for
retrieve) narrows the queryset before the framework materializes it with `.first()`.

## Why it lives on the spec

This library deliberately doesn't ship a filtering DSL — for an HTTP list endpoint,
DRF's `filter_backends` (`DjangoFilterBackend`) already do the job. `filter_set`
exists for the two cases `filter_backends` can't serve:

1. **Retrieve selectors.** DRF's `RetrieveModelMixin` never calls
   `filter_queryset`, so a *detail* selector that returns a filtered queryset — a
   "latest matching", a stats-style read scoped by `?since=…` — had no declarative
   hook and had to hand-roll the filtering inside the selector. `filter_set` narrows
   the queryset before the framework `.first()`s it.
2. **Transport-neutral declaration.** A `FilterSet` is fundamentally
   `(data, queryset) -> .qs` — a contract that doesn't care where `data` comes
   from. The same `SelectorSpec` can drive an alternate transport (the
   `djangorestframework-mcp-server` bridge), where the values arrive in a tool-call
   args dict instead of `request.query_params`. Declaring the filter on the spec
   means it's written once and consumed by every transport, instead of each one
   re-implementing it.

The library applies the FilterSet by **duck typing** — it imports nothing, so
`filter_set` adds no dependency. You only need `django-filter` installed to *write*
the FilterSet:

```bash
pip install django-filter
```

## Define the pieces

A model:

```python
# blog/models.py
from django.db import models


class Post(models.Model):
    title = models.CharField(max_length=200)
    published = models.BooleanField(default=False)
    views = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
```

A `FilterSet` describing the filterable shape:

```python
# blog/filters.py
import django_filters

from blog.models import Post


class PostFilterSet(django_filters.FilterSet):
    published = django_filters.BooleanFilter()
    min_views = django_filters.NumberFilter(field_name="views", lookup_expr="gte")
    created_after = django_filters.DateTimeFilter(field_name="created_at", lookup_expr="gte")

    class Meta:
        model = Post
        fields = ["published", "min_views", "created_after"]
```

A selector that returns a raw queryset — no filtering inside it:

```python
# blog/selectors.py
from django.db.models import QuerySet

from blog.models import Post


def list_posts(*, user) -> QuerySet[Post]:
    """Return every post the caller may see — scoping only, no filtering."""
    return Post.objects.for_user(user)  # your scoping manager
```

Wire it on the spec:

```python
from rest_framework_services import SelectorKind, SelectorSpec, SelectorViewSet

from blog.filters import PostFilterSet
from blog.selectors import list_posts
from blog.serializers import PostSerializer


class PostViewSet(SelectorViewSet):
    queryset = Post.objects.all()
    action_specs = {
        "list": SelectorSpec(
            kind=SelectorKind.LIST,
            selector=list_posts,
            output_serializer=PostSerializer,
            filter_set=PostFilterSet,
            select_related=["author"],   # composes with shaping
        ),
    }
```

`GET /posts/?published=true&min_views=100` narrows the queryset; omitting the
params is a no-op (a FilterSet with no matching data returns the queryset
unchanged).

### On a retrieve selector

The same field works on a `RETRIEVE` spec, where it closes the gap DRF leaves —
the selector returns a queryset and `filter_set` narrows it before `.first()`:

```python
def latest_post(*, user) -> QuerySet[Post]:
    return Post.objects.for_user(user).order_by("-created_at")


class LatestPostView(SelectorRetrieveView):
    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=latest_post,
        output_serializer=PostSerializer,
        filter_set=PostFilterSet,
    )
```

`GET /latest-post/?published=true` returns the most recent *published* post.

## `filter_set` replaces `DjangoFilterBackend` on the list path

On the **list** path DRF's `list()` already runs `filter_queryset()` over the
view's `filter_backends`. `DjangoFilterBackend` does exactly
`filterset_class(query_params, queryset, request).qs` — the same thing
`filter_set` does — so wiring **both** for one action would filter the queryset
twice.

The rule: **`filter_set` replaces `DjangoFilterBackend`.** If you set `filter_set`,
don't also list `DjangoFilterBackend` in that view's `filter_backends`. The library
enforces it — a list selector spec that carries `filter_set` while the view also
wires `DjangoFilterBackend` raises `ImproperlyConfigured` at `as_view()` time:

```python
class PostViewSet(SelectorViewSet):
    filter_backends = [DjangoFilterBackend]   # ← conflicts with filter_set below
    action_specs = {
        "list": SelectorSpec(
            kind=SelectorKind.LIST,
            selector=list_posts,
            filter_set=PostFilterSet,         # ← raises at as_view()
        ),
    }
```

Retrieve has no such conflict: the selector retrieve path overrides `get_object()`
and never calls `filter_queryset`, so `filter_set` is the only filter applied.

## OpenAPI schema

With the [OpenAPI integration](../openapi.md) enabled (`enable_openapi()` +
drf-spectacular), a spec-level `filter_set` documents the **same** query
parameters a view-level `filterset_class` + `DjangoFilterBackend` would — same
names, types, enums, ordering enum + description, `style`/`explode`, and required
flags. Moving a FilterSet off the view and onto the spec therefore leaves the
generated OpenAPI document unchanged, so client codegen and "schema must not
change" review gates keep passing.

The parameters are emitted on **list** operations, matching drf-spectacular's own
behaviour (it documents filter parameters only for list views). A retrieve
selector still filters at runtime — `filter_set` narrows the queryset before
`.first()` — but, like a view-level FilterSet on a detail route, contributes no
query parameters to the detail operation's schema. User `@extend_schema(parameters=...)`
overrides continue to win. The introspection needs the `[filter]` extra
(`django-filter`); without it, schema generation is unaffected.

## When to use `kwargs` instead

`filter_set` earns its place only when the selector returns a **queryset**. If a
selector returns an `.aggregate()` dict or any other computed object, a FilterSet
can't apply to it — those `?since=…` params are *computation inputs*, not queryset
filters. Reach for `kwargs` / `get_selector_kwargs()` there and let the selector
consume them:

```python
def post_stats(*, request) -> dict[str, int]:
    since = request.query_params.get("since")
    qs = Post.objects.all()
    if since:
        qs = qs.filter(created_at__gte=since)
    return qs.aggregate(total=Count("id"), views=Sum("views"))  # not a QuerySet


class PostStatsView(SelectorRetrieveView):
    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=post_stats,           # returns a dict, so no filter_set
        output_serializer=PostStatsSerializer,
    )
```

Setting `filter_set` (or any shaping field) on a selector that returns a
non-queryset raises `ImproperlyConfigured` at request time — the loud failure is
the signal you've reached past the boundary.
