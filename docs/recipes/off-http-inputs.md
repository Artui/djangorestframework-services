# URL-derived and required inputs off-HTTP

Over HTTP a nested route supplies its captures: `/projects/{project_pk}/widgets/`
puts `project_pk` into `view.kwargs`, and the framework spreads that into every
selector pool. Off-HTTP — under an MCP tool, an agent toolset, a management
command, a task runner — there is no route, so those inputs have to arrive some
other way, and a caller has to be *told* they exist.

This recipe covers the four pieces: making a URL-derived input discoverable,
declaring it **required**, hiding an input the caller has no business setting,
and saying in prose what one is for. It also says which inputs a spread service
with no input serializer declares.

## The channel: `build_offline_context(kwargs=…)`

`kwargs=` is the off-HTTP counterpart of `view.kwargs`. Everything you pass there
is spread into the selector and target pools exactly as a route capture would be
— authoritative over the spec params, below a `spec.kwargs` provider.

```python
from rest_framework_services import build_offline_context, dispatch_spec

context = build_offline_context(user, {"status": "active"}, kwargs={"project_pk": 7})
result = dispatch_spec(
    spec, user=user, params={"status": "active"}, request=context.request, view=context.view
)
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

!!! warning "Import the `TypedDict` at runtime"
    The declared surface is read from the resolved annotation, so the
    `TypedDict` has to be importable when the callable is dispatched — an import
    parked under `if TYPE_CHECKING:` is not. With
    `UnknownArguments.REJECT` the annotation is the entire basis for refusing an
    argument, so an unresolvable one raises `ImproperlyConfigured` on dispatch
    rather than quietly letting every argument through. The permissive policies
    read an unresolvable annotation as an open `**kwargs`, as they always have.

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

A plain keyword parameter is the other case. `get_widget(user, *, pk)` cannot run
without `pk` either, and its signature already says so, because `pk` has no
default. A transport that passes `supplied=` to `spec_to_json_schema` advertises
such a parameter as required without a marker, and drops the names it fills
itself along with the reserved pool seeds, which no caller can send. Without
`supplied`, only the marker makes a parameter required. See
[what a transport supplies](../reference/jsonschema.md#what-a-transport-supplies-supplied).

Dispatch enforces that one without a marker too. A parameter with no default that
nothing filled — the caller did not send it, a `kwargs=` provider declined it with
`UNSET`, or there is no provider — is refused with the same
`ServiceValidationError` rather than reaching the callable as a `TypeError`, and
one message lists every missing name, marked or not:
`{"non_field_errors": ["Missing required argument(s): 'pk', 'tenant'."]}`. A
parameter with a default, `**kwargs`, and a positional-only parameter are never
missing. Neither is a reserved pool seed such as `data` or `instance`: client
input cannot carry one, so requiring it is a configuration error rather than an
argument a caller could send, and dispatch leaves it to fail as one. The same goes
for a `NotClientInput` parameter and for `view`, whose caller values dispatch drops
(see [below](#hiding-provider-owned-inputs-notclientinput)), so naming one would
ask the caller for a value it then refuses; and for every parameter of a
`functools.wraps` wrapper that takes `**kwargs`, which may fill any of them
itself, as a decorator injecting a scope does. Each of those that nothing fills
fails as the `TypeError` it always did.

Two callables are not checked at all, because no caller argument reaches their
pool: an affordance's `when` condition, which sees only the seeds, and the
`output_selector_spec` re-read, which sees the seeds and the service's result. The
re-read also runs after the service's write has committed, so a validation error
there would tell the caller nothing happened, and a caller that retries on one
would write twice.

!!! note "Over HTTP, a mutation answers `400` and a selector view `500`"
    Both checks live in the dispatch core, which the HTTP views share, so they run
    there too, but only a mutation view maps the refusal to a response: it answers
    `400`, while a selector view still answers `500`, as the `TypeError` did. They
    rarely fire, because the route guarantees its captures, and a mutation's
    `as_view()` refuses a parameter of its service, preconditions or output
    re-read that nothing could feed, unless a `kwargs=` provider or view hook is
    declared. So a mutation reaches the refusal in two ways: a declared provider
    or hook that leaves a service or precondition parameter out, and a target
    lookup (`instance_selector_spec` / `collection_selector_spec`) needing a
    parameter nothing fills, which `as_view()` never checks, with no hook or
    provider involved. Neither is a server error any more. `as_view()` never
    refuses a selector's parameter as unfed either, and dispatch never refuses the
    output re-read's: past a declared hook, one nothing fills is still the
    `TypeError`. The marker exists because off HTTP there is no route to provide
    that guarantee.

## A spread service with no input serializer

A `ServiceSpec` with no `input_serializer`, dispatched with a `SPREAD_*`
`argument_binding`, has nothing but its service's signature to read the caller's
input against. So the service's own parameters are declared input, beside the
keys of the target lookup:

```python
def close_ticket(*, instance: Ticket, reason: str, notify: bool = False) -> Ticket: ...


spec = ServiceSpec(
    service=close_ticket,
    instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=ticket_by_pk),
)
dispatch_spec(
    spec,
    user=user,
    params={"pk": 7, "reason": "duplicate"},
    argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS,
)
```

`pk` goes to the lookup and `reason` to the service, under every
`UnknownArguments` policy. Leaving `reason` out is
`Missing required argument(s): 'reason'.` under every policy too, and `notify`
keeps its default unless the caller sends it.

| The caller sends… | `IGNORE` | `REJECT` | `PASSTHROUGH` |
| --- | --- | --- | --- |
| a parameter of the service | delivered | delivered | delivered |
| a key neither the service nor the lookup declares | dropped | refused | delivered |

A parameter counts when the caller could fill it by name: a keyword parameter,
or a key of an `Unpack[TypedDict]` `**kwargs`. These do not count, and so are
unknown keys:

- a reserved pool seed such as `user`, `data` or `instance`, or a seed your
  `PoolSeeds` registers. The pool's value fills it, never the caller's;
- a positional-only parameter, which dispatch never passes by name;
- a `NotClientInput` parameter, or one named `view`, whose value the caller never
  supplies (see below).

A bare `**kwargs` declares every name, so the set is open: nothing is refused, and
every key the caller sent reaches the service but two kinds. The reserved seeds
never do. And **the target lookup's keys reach the service only by name**: the
lookup consumed `pk` to find the `instance` the service receives, so
`def update(*, instance, **changes)` does not find `pk` in `changes`, where
applying them would write it onto the row, while
`def update(*, instance, pk, **changes)` names it and receives it. A key the
lookup reads only through its own `**kwargs` or its `filter_set` is not one it
names, so it still reaches the service. An annotated `**kwargs` whose `TypedDict`
cannot be resolved at runtime makes `REJECT` raise `ImproperlyConfigured`, as it
does on a selector, and the other two policies treat it as open, the same way.

With no serializer, a service that declares `data` receives what the spread
carries: the parameters it took by name, plus the `PASSTHROUGH` extras.

Every other spec keeps the set it had. Under `BUNDLE`, which `AUTO` resolves to
for a service, nothing is spread, so a service parameter is still an unknown key
there. An `input_serializer` declares the input itself, and a `many=True` spec
takes its items in one list.

## Hiding provider-owned inputs: `NotClientInput`

Reflection advertises *every* declared key — including ones a `spec.kwargs`
provider fills in from request state, which the caller should never set:

```python
class WidgetExtras(HttpExtras[MyUser], total=False):
    project_pk: Annotated[int, InputRequired]
    team_role: Annotated[str, NotClientInput]  # resolved by spec.kwargs
```

`team_role` disappears from `properties`, and the caller's value for it never
reaches the callable. `dispatch_spec` / `adispatch_spec` drop it from the caller's
input before the spread, under every `UnknownArguments` policy:

| The caller sends `team_role`, and the spec is… | What happens |
| --- | --- |
| closed (no `filter_set`, no bare `**kwargs`), under `REJECT` | refused: `Unexpected argument(s): 'team_role'.` |
| closed, under `IGNORE` or `PASSTHROUGH` | the value is dropped |
| open, under any policy | the value is dropped |

The provider still fills it in. So can a route capture (`view.kwargs`, or
`build_offline_context(kwargs=…)` off HTTP), a registered pool seed, or the
parameter's own default; those are channels the caller does not control. The
same holds for a service whose input is spread, which never receives the key from
`PASSTHROUGH` either, and for a service's `instance_selector_spec` /
`collection_selector_spec` lookup, whose pool is spread from the caller's
arguments. Over HTTP a selector view spreads nothing, so the lookups are the one
place the marker changes what an HTTP request can do: a request body's value for a
key the lookup marks hidden is dropped there too.

A hidden parameter with no default that nothing fills is not named as a missing
argument either: the caller could not send it, so the call fails as the author's
`TypeError`.

!!! warning "What is left to decide the value"
    Because the caller's value is gone before the precedence is applied, neither
    `SPREAD_CALLER_WINS` nor a provider declining with `UNSET` can let it back in.
    What a declining provider leaves is the parameter's default. For a **scoping**
    key whose default reads as "everything", that is still a cross-scope read, so
    a provider owning a scoping key must always resolve it. A key the callable
    does *not* mark is still decided by the precedence: under
    `SPREAD_AUTHOR_WINS` the provider overrides caller input, and opting into
    `SPREAD_CALLER_WINS` on a scoped spec gives the caller the last word.

Marking a key both `InputRequired` and `NotClientInput` raises — the caller cannot
be required to supply a value it is never told about.

### `view` is never caller input

No pool carries `view`. A selector or service that wants the calling view takes it
from a `kwargs=` provider or a `get_*_kwargs` view hook, or from a route capture
of that name. The input schema hides it from a selector's parameters, as it hides
`request` and `user`, so dispatch treats `view` as though every callable marked it
`NotClientInput`: the caller's value is dropped at every site above, under every
policy, `REJECT` refuses it as `Unexpected argument(s): 'view'.` on a closed spec,
and a required `view` nothing filled is never named as a missing argument. It
fails as the `TypeError` it always did, because no value a caller could send would
be the view.

Over HTTP a provider, a hook and a route capture deliver the real view as they
did. As with a hidden key, the one thing an HTTP request can no longer do is have
its body's `view` reach a mutation's target lookup.

## Describing an input: `InputDescription`

`project_pk` now reaches a schema-driven caller as `{"type": "integer"}` and a
name. A model calling the tool has to guess what a project id is *for* here, and
the type says nothing about it.

A serializer field would carry `help_text`. A `TypedDict` key has no field, and
neither of the two markers above can carry text — both are singletons, so
`InputRequiredType()` returns the one instance and has nowhere to put per-key
prose. `InputDescription` is the third marker, and it is a value rather than a
singleton for exactly that reason:

```python
class WidgetExtras(HttpExtras[MyUser], total=False):
    project_pk: Annotated[
        int, InputRequired, InputDescription("The project whose widgets to list.")
    ]
```

The text lands on the property as `description` — the same key the serializer
path fills from `help_text` and the filter path from a filter's — so one
project's inputs read the same way whichever side of a spec they arrive on. Order
inside `Annotated` does not matter, and metadata belonging to other libraries is
left alone.

The alternative was declaring the same input twice: once in the `TypedDict` the
callable actually reads, and once in a serializer written only so one transport
could describe it. Two declarations of one input drift, and the one that drifts
is the one nothing executes.

!!! warning "Two refusals"
    Two `InputDescription` markers on one input raise — a schema publishes one
    description, and choosing between them would be an arbitrary rule to
    remember. So does an `InputDescription` beside `NotClientInput`: that marker
    drops the key from the schema, so the sentence has no caller left to reach.
    Neither is a contradiction the way `InputRequired` + `NotClientInput` is —
    they are declarations that would decide nothing, which is the thing this
    package refuses to do quietly.

To describe the *operation* rather than one of its inputs, see
[a spec-level title and description](../reference/jsonschema.md#a-spec-level-title-and-description).

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
| …and the caller must never set it | `Annotated[T, NotClientInput]`, filled by a provider, a route capture, a pool seed or the default |
| …and the name alone doesn't say what it is for | `Annotated[T, InputDescription("…")]` |
| read only by a `spec.kwargs` provider off `view.kwargs` | `UrlKwarg(..., required=…)` |
| read off `request.query_params` to shape output | `QueryParam(...)` |

A key can be *both* reflected and `UrlKwarg`-registered — a `project_pk` the
selector reads *and* a scoping provider reads off `view.kwargs`. The adapter's
schema merge dedupes to one property (the explicit registration wins), the
registration pops the argument into `kwargs=`, and the authoritative spread still
delivers it to the selector pool, so both readers see it.

[pep692]: https://peps.python.org/pep-0692/
