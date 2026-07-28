# URL-derived and required inputs off-HTTP

Over HTTP a nested route supplies its captures: `/projects/{project_pk}/widgets/`
puts `project_pk` into `view.kwargs`, and the framework spreads that into every
selector pool. Off-HTTP — under an MCP tool, an agent toolset, a management
command, a task runner — there is no route, so those inputs have to arrive some
other way, and a caller has to be *told* they exist.

This recipe covers the three pieces: making a URL-derived input discoverable,
declaring it **required**, and hiding an input the caller has no business setting.

## The channel: `build_offline_context(kwargs=…)`

`kwargs=` is the off-HTTP counterpart of `view.kwargs`. Everything you pass there
is spread into the selector and target pools exactly as a route capture would be
— authoritative over the spec params, below a `spec.kwargs` provider.

```python
from rest_framework_services import build_offline_context, dispatch_spec

context = build_offline_context(user, {"status": "active"}, kwargs={"project_pk": 7})
result = dispatch_spec(spec, user=user, params={"status": "active"},
                       request=context.request, view=context.view)
```

## Discoverability: the TypedDict *is* the declaration

A selector that reads its extras through the blessed strict-typing idiom already
declares its input surface, and `spec_to_json_schema` reflects it — one property
per key, no registration anywhere:

```python
from typing import Annotated
from typing_extensions import Unpack
from rest_framework_services import HttpExtras, implements, ListSelector

class WidgetExtras(HttpExtras[MyUser], total=False):
    project_pk: int

@implements(ListSelector[Widget])
def list_widgets(**extras: Unpack[WidgetExtras]) -> list[Widget]:
    return Widget.objects.filter(project_id=extras["project_pk"])
```

The inherited `request` / `user` seeds are excluded automatically — they are
transport-controlled, never caller input.

## Requiredness: `InputRequired`

`list_widgets` above cannot run without `project_pk`, but the schema says the key
is optional, and a caller that omits it gets a `KeyError` from inside dispatch.

The obvious fix — making the key required in the `TypedDict` — **does not work**,
and the reason is worth knowing. Under [PEP 692][pep692], a required key in
`Unpack[...]` makes the function reject callers that omit it, so it stops being
assignable to `ListSelector` / `RetrieveSelector` / the service Protocols. That is
also why [`HttpExtras`][rest_framework_services.types.http_extras.HttpExtras] mandates `total=False`.
Protocol conformance and an honest schema are mutually exclusive through the
`TypedDict`'s own totality.

`InputRequired` carries the signal in `Annotated` metadata instead, which has no
effect on the type system:

```python
class WidgetExtras(HttpExtras[MyUser], total=False):
    project_pk: Annotated[int, InputRequired]
```

Two things follow. The key joins the schema's `required` list, so a
schema-driven caller is told up front. And `dispatch_spec` / `adispatch_spec`
raise [`ServiceValidationError`][rest_framework_services.exceptions.service_validation_error.ServiceValidationError]
when the key is absent — a caller-visible validation failure every transport
already maps, instead of a bare `KeyError` that none of them do.

**Any channel satisfies the requirement**: the caller's `params`, the `kwargs=`
channel above, or a `spec.kwargs` provider. The marker says the value must
arrive, not where from. A provider that *declines* with `UNSET` does not satisfy
it — declining removes the key from the pool entirely.

!!! note "Off-HTTP only, by design"
    Enforcement lives in `dispatch_spec` / `adispatch_spec`. On the HTTP path the
    route *is* the guarantee — a capture the URLconf doesn't declare is a wiring
    bug, not caller input. The marker exists precisely because off-HTTP there is
    no route to provide that guarantee.

## Hiding provider-owned inputs: `NotClientInput`

Reflection advertises *every* declared key — including ones a `spec.kwargs`
provider fills in from request state, which the caller should never set:

```python
class WidgetExtras(HttpExtras[MyUser], total=False):
    project_pk: Annotated[int, InputRequired]
    team_role: Annotated[str, NotClientInput]   # resolved by spec.kwargs
```

`team_role` disappears from `properties`, and because it is no longer a declared
input, `UnknownArguments.REJECT` treats a caller that supplies it as passing an
unknown argument. Delivery is untouched: the provider still fills it in.

!!! warning "Hiding a key is not what makes it safe"
    The security property is the `SPREAD_AUTHOR_WINS` precedence — the author's
    provider overrides caller input — not the absence of an advertisement. Two
    corollaries survive unchanged: a provider owning a **scoping** key must always
    resolve it and never decline via `UNSET` (declining lets the caller's value
    through, which for a scoping key is a cross-scope read), and opting into
    `SPREAD_CALLER_WINS` on a scoped spec voids the guarantee whether or not the
    key is hidden.

Marking a key both `InputRequired` and `NotClientInput` raises — the caller cannot
be required to supply a value it is never told about.

## Values the callable never declares: `UrlKwarg`

Reflection covers keys the callable's own signature declares. It cannot cover a
value read only by a `spec.kwargs` provider off `view.kwargs`, because that value
appears in no signature at all — nor a closed-surface spec whose route capture
must still be caller-suppliable.

For those, a transport adapter registers a
[`UrlKwarg`][rest_framework_services.types.url_kwarg.UrlKwarg]:

```python
from rest_framework_services import UrlKwarg

UrlKwarg("project_pk", type="integer", description="Owning project.", required=True)
```

The adapter merges its `json_schema()` into the operation's input schema, pops the
argument out of the caller's arguments, and routes it to
`build_offline_context(kwargs=…)`. `required=True` is the registered-declaration
counterpart of the `InputRequired` marker: both land the name in the schema's
`required` list, and they differ only in where the key is declared.

`QueryParam` is the sibling for read-shaping values that belong on
`request.query_params` (django-restql field selection, a serializer that branches
on the query string). It has **no** `required` flag on purpose — omitting a
read-shaping param is legitimate by construction.

Both types live here rather than in each adapter so the same declaration means the
same thing on every transport. Adapters validate a registration set with
[`validate_channel_names`][rest_framework_services.types.validate_channel_names.validate_channel_names], which
always includes `RESERVED_POOL_SEEDS` and takes the transport's own reserved
pagination names on top.

## Which one do I reach for?

| The value is… | Use |
| --- | --- |
| read by the callable from its own `**extras` | nothing — reflection covers it |
| …and the spec can't run without it | `Annotated[T, InputRequired]` |
| …and the caller must never set it | `Annotated[T, NotClientInput]` |
| read only by a `spec.kwargs` provider off `view.kwargs` | `UrlKwarg(..., required=…)` |
| read off `request.query_params` to shape output | `QueryParam(...)` |

A key can be *both* reflected and `UrlKwarg`-registered — a `project_pk` the
selector reads *and* a scoping provider reads off `view.kwargs`. The adapter's
schema merge dedupes to one property (the explicit registration wins), the
registration pops the argument into `kwargs=`, and the authoritative spread still
delivers it to the selector pool, so both readers see it.

[pep692]: https://peps.python.org/pep-0692/
