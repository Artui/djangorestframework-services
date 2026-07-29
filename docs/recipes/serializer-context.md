# Customise serializer context

DRF's `get_serializer_context()` lives on every `GenericAPIView` and
returns `{"request", "view", "format"}` by default. When the standard
viewset shape uses one serializer per action it's enough — but service-
backed views run **two** serializers per request (input and output), and
sometimes those two need different context. The library plumbs that
without forcing you to override `get_serializer_context()` and branch on
direction.

Four layers, later wins on overlap:

1. **DRF default** — `view.get_serializer_context()`. Always applied.
2. **Directional fallback** — `get_input_serializer_context()` /
   `get_output_serializer_context()` on the view. Skipped when the method
   is absent, so plain DRF viewsets work unchanged.
3. **Per-action override** — `get_<action>_input_serializer_context()` /
   `get_<action>_output_serializer_context()`. Viewset-only; skipped on
   standalone single-purpose views.
4. **Per-spec callable** — `ServiceSpec.input_serializer_context` /
   `ServiceSpec.output_selector_spec.output_serializer_context` /
   `SelectorSpec.output_serializer_context`. Co-located with the spec
   that backs the action. Has the final say.

The resolver is wired into service-backed views, viewset mixins,
`@service_action`, and `@selector_action`. The standalone
`SelectorListView` / `SelectorRetrieveView` override
`get_serializer_context()` directly; the `SelectorListMixin` /
`SelectorRetrieveMixin` viewset mixins inherit the same override from
`_ActionSpecsMixin`. Either way, a `SelectorSpec.output_serializer_context`
flows into DRF's `ListModelMixin` / `RetrieveModelMixin` dispatch.

## Different context for input vs. output

A common case: the input serializer needs a "current tenant" for
cross-field validation, but the output serializer doesn't.

```python
class AuthorViewSet(ServiceViewSet):
    queryset = Author.objects.all()
    action_specs = {
        "create": ServiceSpec(
            service=create_author,
            input_serializer=CreateAuthorInput,
            output_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                output_serializer=AuthorOutputSerializer,
            ),
        ),
    }

    def get_input_serializer_context(self):
        return {"tenant": self.request.tenant}
```

Inside the input serializer:

```python
class CreateAuthorInput(serializers.Serializer):
    name = serializers.CharField()

    def validate_name(self, value):
        tenant = self.context["tenant"]
        if Author.objects.filter(tenant=tenant, name=value).exists():
            raise serializers.ValidationError("name already taken in this tenant")
        return value
```

The output serializer keeps the default DRF context.

## Per-action override

When only one action needs the extra key, name the hook after the
action — no branching on `self.action`:

```python
_author_out = SelectorSpec(
    kind=SelectorKind.RETRIEVE,
    output_serializer=AuthorOutputSerializer,
)


class AuthorViewSet(ServiceViewSet):
    action_specs = {
        "create": ServiceSpec(
            service=create_author,
            input_serializer=CreateAuthorInput,
            output_selector_spec=_author_out,
        ),
        "update": ServiceSpec(
            service=update_author,
            input_serializer=UpdateAuthorInput,
            output_selector_spec=_author_out,
        ),
    }

    def get_create_input_serializer_context(self):
        return {"signup_token": self.request.headers.get("X-Signup-Token")}
```

`update` keeps the directional / DRF default; `create` adds the token on
top.

## Per-spec context (the fourth layer)

When context belongs *with* the spec rather than the view, put it on the
spec. This pairs the input/output context with the service/selector it
feeds — no second method on the view, no `if self.action == ...`.

```python
class AuthorViewSet(ServiceViewSet):
    queryset = Author.objects.all()
    action_specs = {
        "create": ServiceSpec(
            service=create_author,
            input_serializer=CreateAuthorInput,
            input_serializer_context=lambda view, request: {
                "tenant": request.tenant,
            },
            output_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                output_serializer=AuthorOutputSerializer,
                output_serializer_context=lambda view, request: {
                    "include_links": "links" in request.query_params,
                },
            ),
        ),
        "list": SelectorSpec(
            kind=SelectorKind.LIST,
            selector=list_authors,
            output_serializer=AuthorOutputSerializer,
            output_serializer_context=lambda view, request: {
                "include_links": True,
            },
        ),
    }
```

`SelectorSpec` carries only `output_serializer_context` — selectors don't
validate input, so there's no symmetrical input hook. On `ServiceSpec`,
the output hook lives on the nested `output_selector_spec`; the input
hook stays at the top level. The callable receives the calling view
(typed as `ServiceView`) and the DRF `Request`.

## Output context that depends on the resolved data

Sometimes the output serializer needs data derived from the very objects
it's about to render — and you want that derived data fetched in a
**single** batched query rather than one query per row. The output
context provider can receive the resolved data and run that one query.

An **output** context provider (the directional
`get_output_serializer_context` hook, the per-action
`get_<action>_output_serializer_context` hook, or the spec-level
`output_serializer_context`) may declare an extra keyword parameter
naming the data about to be serialized:

| Action | Keyword | Value |
|---|---|---|
| mutation output | `result` | the final (post-`output_selector_spec`) instance |
| retrieve | `instance` | the resolved object |
| list | `page` | the paginated object list (the full queryset when pagination is off) |

Every provider is invoked through the framework's keyword pool, so it
declares **only what it needs** — any subset of `view` / `request` plus the
resolved-data extra above, or `**kwargs` for the whole pool. A
`(view, request)` provider keeps working unchanged (both bind by keyword),
and `lambda *, page: ...` or `lambda request: ...` are equally valid. The
provider always runs *after* the data is resolved, so the value is real, not
a placeholder.

```python
from django.db.models import Count

class PostViewSet(SelectorViewSet):
    queryset = Post.objects.all()
    action_specs = {
        "list": SelectorSpec(
            kind=SelectorKind.LIST,
            selector=list_posts,
            output_serializer=PostOutputSerializer,
            # One query for the whole page, keyed on the page's ids.
            output_serializer_context=lambda view, request, *, page: {
                "comment_counts": dict(
                    Comment.objects.filter(post__in=page)
                    .values_list("post")
                    .annotate(n=Count("id"))
                ),
            },
        ),
    }
```

The serializer reads `self.context["comment_counts"][obj.id]` per row,
with no extra query. The same shape works for a mutation
(`*, result`) or a retrieve (`*, instance`), and on the view-method
hooks too — e.g. `def get_list_output_serializer_context(self, *, page): ...`.

For an unpaginated list the `page` value is the full queryset; reading
ids off it (`[p.id for p in page]`) reuses the same evaluated queryset
DRF serializes, so it still costs one batched query, not two.

The **input** context provider has no such extra — there is no resolved
output before the service runs.

## On standalone views

Standalone mutation views (`ServiceCreateView`, `ServiceUpdateView`,
`ServiceDeleteView`) and `@service_action` honour the directional hooks
(`get_input_serializer_context` / `get_output_serializer_context`) plus
the spec layer (`ServiceSpec.input_serializer_context` and
`ServiceSpec.output_selector_spec.output_serializer_context`). The
per-action hooks only apply
where there is an action — i.e. inside viewset mixins and
`@service_action` / `@selector_action`.

Standalone `SelectorListView` / `SelectorRetrieveView` honor
`SelectorSpec.output_serializer_context` through their
`get_serializer_context()` override. They do **not** honor the directional
`get_output_serializer_context` hook there — that hook is reserved for the
mutation flow's input/output split. If you want a shared "always add X to
the context" on a selector view, override `get_serializer_context()`
directly.

## Off the HTTP path

There is no view to call `get_serializer_context()` on when a spec is
dispatched from an MCP tool, a Pydantic-AI toolset, or a management command —
so `dispatch_spec` / `render_spec_output` synthesize DRF's baseline instead
(`base_serializer_context`), and layer the spec's provider over it:

| Layer | HTTP | Off HTTP |
|---|---|---|
| 1. DRF default | `view.get_serializer_context()` | `{"request", "format": None, "view"}` from the synthetic pair `build_offline_context` builds |
| 2. Directional hook | `get_<direction>_serializer_context()` | — (no view to host it) |
| 3. Per-action hook | `get_<action>_<direction>_serializer_context()` | — |
| 4. Per-spec callable | `*_serializer_context` on the spec | same, and still has the final say |

So a serializer that reads `self.context["request"]` unguarded — for
`request.user`, an ownership check in a `SerializerMethodField`, a
`PrimaryKeyRelatedField` queryset scoped to the caller — renders the same over
both transports. `request.user` is the `user` you passed to
`build_offline_context`.

The two view-hosted layers have no off-HTTP equivalent by design: they are
view configuration, and off HTTP there is no view to configure. Context that
must reach *every* transport belongs on the spec (layer 4), which is the layer
both paths share.

### Absolute URLs off the HTTP path

`build_absolute_uri()` — which DRF's `FileField`, `HyperlinkedIdentityField`, and
`HyperlinkedRelatedField` call whenever a `request` is in the context — needs an
origin, and a process with no ambient request has none. Two ways to give it one:

```python
# The transport has a real request (the MCP server): pass it through.
build_offline_context(user, http_request=request)

# It doesn't (a toolset, a management command, a worker): name the origin.
build_offline_context(user, host="https://app.example.com")
```

`host` accepts `"example.com"`, `"example.com:8000"`, or a full origin whose
scheme decides whether links are `https`. It is **ignored when `http_request` is
supplied**, so a caller can pass both unconditionally — the ambient request when
there is one, the configured host when there isn't. It is not validated against
`ALLOWED_HOSTS`: that setting rejects spoofed `Host` headers from untrusted
clients, and this value is your own, so a worker can render links for a site it
doesn't itself serve.

Left unset, `build_absolute_uri()` returns the **relative** URL rather than
raising. There is deliberately no default: only your project knows its public
origin, and inferring one — the first `ALLOWED_HOSTS` entry, say, which is an
authorization list and is routinely a wildcard or an internal load-balancer name
— would emit confidently-wrong links that look valid. A relative URL is what
those DRF fields already fall back to when there's no request in the context at
all.
