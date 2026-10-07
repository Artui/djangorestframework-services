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
    Only the `**kwargs` annotation itself counts here: another parameter, or
    another key of the `TypedDict`, naming something imported under
    `if TYPE_CHECKING:` leaves the surface known, because each annotation is
    resolved on its own.

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
when the key is absent and the caller could have sent it (see the table below) —
a caller-visible validation failure every transport already maps, instead of a
bare `KeyError` that none of them do.

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

Dispatch enforces that one without a marker too. A parameter with no default
that nothing filled — the caller did not send it, a `kwargs=` provider declined it
with `UNSET`, or there is no provider — is refused with the same
`ServiceValidationError` rather than reaching the callable as a `TypeError`, and
one message lists every missing name, marked or not:
`{"non_field_errors": ["Missing required argument(s): 'pk', 'tenant'."]}`.

Both checks name a key only where **the caller could have filled it**, which is
the input its call declares:

| The callable | What a caller could fill |
| --- | --- |
| a selector under a `SPREAD_*` binding, which `AUTO` resolves to for a selector, and its preconditions | the selector's parameters: every name under a `filter_set` or a bare `**kwargs` |
| a selector under `BUNDLE`, as an HTTP selector view dispatches it, and its preconditions | nothing: no caller input reaches the pool |
| a service under `BUNDLE` (the default), and its preconditions | nothing: the caller's input arrives as `data`, never by name |
| a service under a `SPREAD_*` binding, and its preconditions | what `REJECT` admits: the input serializer's fields or, with none, the service's own parameters, beside the target lookup's keys that reach the service, which are none of them beside an input serializer and otherwise those the service names; every name where a bare `**kwargs` opens the set, less the lookup's keys the service does not name |
| a target lookup (`instance_selector_spec` / `collection_selector_spec`) | the lookup selector's parameters, whatever the service's binding, since its pool is spread from the caller's arguments |

The table reads declared input, so a precondition parameter its selector or
service does not declare, left unfilled, fails as the callable's `TypeError`, even
where `IGNORE` or `PASSTHROUGH` would deliver a resend carrying it; naming it
would send a `REJECT` caller from a missing argument to an unexpected one. An
author who wants such a parameter refused as missing declares it on the selector
or service. And an input serializer field with `source=` reaches the spread under
its source, not its own name, so a service or precondition naming the field is
asked for it, and the resend carrying it still fails as the `TypeError`; name the
source instead.

Less, in every case, a key any callable in the call marks `NotClientInput` (see
[below](#hiding-provider-owned-inputs-notclientinput)), and the keys the caller
sent: a sent key that never arrived was dropped by something the caller cannot
change. So a refusal only ever names a key the caller can send and has not, and a
client that resends with that key is past it, rather than retrying on the same
error forever, as an agent would. A parameter with a default, `**kwargs`, a
positional-only parameter, a reserved pool seed such as `data` or `instance`, and
every parameter of a `functools.wraps` wrapper that takes `**kwargs` (which may
fill any of them itself, as a decorator injecting a scope does) are never missing.
Anything else that nothing fills, a provider gap under `BUNDLE` say, is a
configuration error no client can fix, and fails as the callable's own `TypeError`,
as it always did. The sibling kernel django-service-specs draws the same line.
That holds for an `InputRequired` key too: the marker says the value must arrive,
but where no caller could send it, as for any key under `BUNDLE`, a refusal naming
it would only be answered by the same refusal, so it fails as the callable's own
error, a `TypeError` for a parameter, and whatever reading it raises for a key of
an unpacked `TypedDict`, such as `KeyError`.

Two callables are not checked at all, for a marked key or a plain parameter,
because no caller argument reaches their pool: an affordance's `when` condition,
which sees only the seeds, and the `output_selector_spec` re-read, which sees the seeds and the service's result. The
re-read also runs after the service's write has committed, so a validation error
there would tell the caller nothing happened, and a caller that retries on one
would write twice.

!!! note "Over HTTP, a lookup gap is a `400` and a hook gap a `500`"
    Both checks live in the dispatch core, which the HTTP views share, so they run
    there too, but only a mutation view maps the refusal to a response, a `400`. A
    selector view dispatches `BUNDLE`, so nothing it calls is refused, and a gap
    answers `500` as the `TypeError` it is. They rarely fire,
    because the route guarantees its captures, and a mutation's `as_view()`
    refuses a parameter of its service, preconditions or output re-read that
    nothing could feed, unless a `kwargs=` provider or view hook is declared.
    A mutation dispatches its service `BUNDLE`, so a declared provider or hook
    that leaves a service or precondition parameter out is not refused, marked
    `InputRequired` or not: no request could fill it, and it fails as the
    `TypeError` it is, a `500`. A
    target lookup parameter nothing fills is refused with a `400`, because a
    mutation reads the lookup's keys from the request body, which `as_view()`
    never checks. `as_view()` never refuses a selector's parameter as unfed
    either, and dispatch never refuses the output re-read's: past a declared
    hook, one nothing fills is still the `TypeError`. The marker exists because
    off HTTP there is no route to provide that guarantee.

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
  supplies (see below). That includes a key a precondition or the target lookup
  marks, even where the service names it plainly.

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

The input schema says the same when it is told the binding. Pass the one you
dispatch with to `spec_to_json_schema`, and it lists the service's parameters,
exactly the set the table above calls declared:

```python
spec_to_json_schema(spec, argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS)
# {"type": "object",
#  "properties": {"reason": {"type": "string"}, "notify": {"type": "boolean"}}}
```

The lookup's `pk` is not in it: a transport merges a target lookup's keys
itself, by reflecting the lookup's `SelectorSpec`. A bare `**kwargs` lists the
parameters the service names and states no `additionalProperties`, so a
transport decides from its own policy whether to close the tool. Without the
argument, `AUTO` is `BUNDLE` and the schema is the bare `{"type": "object"}` it
always was. See
[a spreading service's own parameters](../reference/jsonschema.md#a-spreading-services-own-parameters-argument_binding).

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

The provider still fills it in. So can a registered pool seed, a service's
`input_data`, the parameter's own default, or, for a selector and its
preconditions or a service's target lookup, a route capture (`view.kwargs`, or
`build_offline_context(kwargs=…)` off HTTP), which reaches no service's own
pool; those are channels the caller does not control. The same holds for a service whose input is spread, which never receives the key from
`PASSTHROUGH` either, and for a service's `instance_selector_spec` /
`collection_selector_spec` lookup, whose pool is spread from the caller's
arguments. Over HTTP a selector view spreads nothing, so the lookups are the one
place the marker changes what an HTTP request can do: a request body's value for a
key the lookup marks hidden is dropped there too.

**A key is server-owned for the whole call** once any callable in it marks it:
the selector or service, any of `spec.preconditions`, or the target lookup.
The callables of one call read the same caller input, so a key one of them
hides is dropped before any of them sees it, and is gone from the declared set
`REJECT` admits and from the input schema, wherever else it is a plain
parameter. A field the `input_serializer` declares under that name is the one
exception, because it is not a parameter: it stays declared and listed, and the
caller's value reaches the service only in `data`, never the spread, since a
same-named field can mean something else, such as a destination tenant beside
the server's current one. A precondition taking `tenant: Annotated[int, NotClientInput]` beside
a selector that reads `tenant` plainly makes the selector's `tenant` the
provider's too. A key the target lookup hides never reaches an open spread
service's `**changes`, and a service naming it receives the server's value: the
provider's, `input_data`'s or its default. The set is public as
[`server_owned_keys(spec)`](../reference/dispatch.md#server_owned_keys), for a
transport that builds an input schema of its own and must leave out the same
keys.

A hidden parameter with no default that nothing fills is not named as a missing
argument either: the caller could not send it, so the call fails as the author's
`TypeError`.

`input_data` (or a `get_input_data` view hook) is the spec author's own code, merged
over the caller's input with its keys winning, so its value for a hidden key is the
server's and is never dropped. Where the caller sends the same key, the server's
value is the one that arrives. It reaches the service through an input serializer
field of that name under every policy, as a `PASSTHROUGH` extra, and through a
spread service's bare `**kwargs`, except a key the target lookup names, its `pk`
or a key it marks hidden: that one reaches a spread service only through a
parameter of the same name, never its `**kwargs`, as the caller's `pk` does
([above](#a-spread-service-with-no-input-serializer)). The marker still keeps the key out of the declared
set, so on a closed spec with no such field `IGNORE` drops it and `REJECT`, which
judges the merged arguments, refuses it, as it refuses any `input_data` key nothing
declares.

!!! warning "What is left to decide the value"
    Because the caller's value is gone before the precedence is applied, neither
    `SPREAD_CALLER_WINS` nor a provider declining with `UNSET` can let it back in.
    What a declining provider leaves is the parameter's default. A caller that used
    to fill the key through its arguments, such as a task runner or a test, loses
    the value the same way: silently where the parameter has a default, and with
    the callable's own `TypeError` where it has none, which over HTTP is a `500`.
    Move such a value to a provider, `input_data` or a pool seed. For a **scoping**
    key whose default reads as "everything", that is still a cross-scope read, so
    a provider owning a scoping key must always resolve it. A key the callable
    does *not* mark is still decided by the precedence: under
    `SPREAD_AUTHOR_WINS` the provider overrides caller input, and opting into
    `SPREAD_CALLER_WINS` on a scoped spec gives the caller the last word.

Marking a key both `InputRequired` and `NotClientInput` raises — the caller cannot
be required to supply a value it is never told about.

!!! note "Annotations that do not resolve at runtime"
    Each annotation is read on its own, so a parameter or key typed with a name
    imported only under `if TYPE_CHECKING:` costs only itself: the markers on every
    other parameter and key are hidden, required and enforced as written. Its own
    markers are read with that name standing in for `Any`, so
    `Annotated[Owner, NotClientInput] | None` is hidden and
    `Annotated[Owner, InputRequired]` is required. Where even that cannot reach a
    marker, as in `Annotated[models.Owner, NotClientInput]` with `models` imported
    for type checking only, the marker is honoured by its name, which fails closed:
    an annotation whose text names `NotClientInput` is hidden, and one naming
    `InputRequired` is required. Read that way, an `InputDescription` is not
    published and a marker placed too deep is not refused, so import the names a
    marked annotation uses at runtime where you can. The one thing that stays
    unknown is a `**kwargs: Unpack[Extras]` whose `TypedDict` does not resolve:
    nothing says which keys it has, so none is hidden, `REJECT` refuses to run on
    it and the other policies deliver what the caller sent (see the warning about
    importing the `TypedDict` at runtime, above).

### `view` is never caller input

No pool carries `view`. A selector or service that wants the calling view takes it
from a `kwargs=` provider or a `get_*_kwargs` view hook; a selector or a target
lookup can also take it from a route capture of that name, which reaches no
service's pool. The input schema hides it from a selector's parameters, as it hides
`request` and `user`, so dispatch treats `view` as though every callable marked it
`NotClientInput`: the caller's value is dropped at every site above, under every
policy, `REJECT` refuses it as `Unexpected argument(s): 'view'.` on a closed spec,
and a required `view` nothing filled is never named as a missing argument. It
fails as the `TypeError` it always did, because no value a caller could send would
be the view.

Over HTTP a provider, a hook and, to a selector or a target lookup, a route
capture deliver the real view as they did. As with a hidden key, the one thing an HTTP request can no longer do is have
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
| …and the caller must never set it | `Annotated[T, NotClientInput]`, filled by a provider, a pool seed, `input_data` or the default, or for a selector or a target lookup a route capture |
| …and the name alone doesn't say what it is for | `Annotated[T, InputDescription("…")]` |
| read only by a `spec.kwargs` provider off `view.kwargs` | `UrlKwarg(..., required=…)` |
| read off `request.query_params` to shape output | `QueryParam(...)` |

A key can be *both* reflected and `UrlKwarg`-registered — a `project_pk` the
selector reads *and* a scoping provider reads off `view.kwargs`. The adapter's
schema merge dedupes to one property (the explicit registration wins), the
registration pops the argument into `kwargs=`, and the authoritative spread still
delivers it to the selector pool, so both readers see it.

[pep692]: https://peps.python.org/pep-0692/
