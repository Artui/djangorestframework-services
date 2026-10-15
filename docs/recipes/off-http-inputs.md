# URL-derived and required inputs off-HTTP

Over HTTP a nested route supplies its captures: `/projects/{project_pk}/widgets/`
puts `project_pk` into `view.kwargs`, and the framework spreads that into every
selector pool. Off-HTTP — under an MCP tool, an agent toolset, a management
command, a task runner — there is no route, so those inputs have to arrive some
other way, and a caller has to be *told* they exist.

This recipe covers the four pieces: making a URL-derived input discoverable,
declaring it **required**, hiding an input the caller has no business setting,
and saying in prose what one is for. It also says which inputs a service with no
input serializer declares, and which validator answers a call
([who validates what](#who-validates-what)).

## The channel: `build_offline_context(kwargs=…)`

`kwargs=` is the off-HTTP counterpart of `view.kwargs`. A name you pass there
reaches the selector and the target lookup exactly as a route capture would,
over the spec params, below a `spec.kwargs` provider, when the spec declares it:
a `UrlKwarg` in its `reads`, or a parameter the selector or target lookup reads.
A capture nothing declares, reads or hides is no argument.

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
refuse a call that lacks it, with a DRF `ValidationError` keyed by the missing
argument (`InvalidArguments`, see
[the kernel's refusals as DRF's](../reference/service-specs.md#the-kernels-refusals-as-drfs)),
which every transport already maps, instead of a bare `KeyError` that none of
them do: `{"project_pk": ["This field is required."]}`, code `required`. The
marker holds the key required whatever its Python default.

**Any channel satisfies the requirement**: the caller's `params`, the `kwargs=`
channel above, or a `spec.kwargs` provider, typed or not. The marker says the
value must arrive, not where from. A typed provider that *declines* with `UNSET`
does not satisfy it: declining removes the key from the pool entirely.

A plain keyword parameter is the other case. `get_widget(user, *, pk)` cannot run
without `pk` either, and its signature already says so, because `pk` has no
default. A transport that passes `supplied=` to `spec_to_json_schema` advertises
such a parameter as required without a marker, and drops the names it fills
itself along with the reserved pool seeds, which no caller can send. Without
`supplied`, only the marker makes a parameter required. See
[what a transport supplies](../reference/jsonschema.md#what-a-transport-supplies-supplied).

Dispatch enforces that one without a marker too. The compiled spec declares a
parameter with no default as required, so a call that lacks it is refused the
same way, every missing name keyed in the one refusal. That holds for the
selector, the target lookup, and a service with no `input_serializer` dispatched
under `SPREAD_AUTHOR_WINS` (an `input_serializer` declares its own required
fields, and under `AUTO` such a service's own parameters are no caller input, so
neither its signature nor a marker on it is enforced; see
[a service with no input serializer](#a-service-with-no-input-serializer)). A name no caller can send is
never required of one: a pool seed, a key any callable of the call marks
`NotClientInput`, or a key a typed `kwargs` provider fills. One of those that
nothing fills is the author's configuration error, raised as the callable's own,
not a refusal naming the key, since no resend could fix it.

## A service with no input serializer

A `ServiceSpec` with no `input_serializer` has nothing but its service's
signature to read the caller's input against. Whether that signature is caller
input is the one question `argument_binding` still answers, and the default
answer is no:

```python
def close_ticket(
    *, instance: Ticket, data: dict, reason: str = "", notify: bool = False
) -> Ticket: ...


spec = ServiceSpec(
    service=close_ticket,
    instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=ticket_by_pk),
)
dispatch_spec(spec, user=user, params={"pk": 7, "reason": "duplicate"})
```

Under `AUTO`, which is `BUNDLE` for a service, `pk` goes to the lookup and
`reason` reaches nobody: the service's own parameters are never the caller's,
so a body cannot set a `hard` or an `is_admin` the service merely declares. The
service receives `data={}` and its declared `reads` by name. Under `REJECT`,
`reason` is refused as `Unexpected argument(s): 'reason'.`

Pass `ArgumentBinding.SPREAD_AUTHOR_WINS` to make the signature the caller's
input:

```python
dispatch_spec(
    spec,
    user=user,
    params={"pk": 7, "reason": "duplicate"},
    argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS,
)
```

Now `reason` reaches the service, a parameter with no default that the caller
leaves out is `{"reason": ["This field is required."]}`, and `notify` keeps its
default unless the caller sends it. An author's value, from a view hook or the
spec's `kwargs` provider, still outranks the caller's on a conflict. `data` is
`{}` here too.

| The caller sends... | `IGNORE` | `REJECT` |
| --- | --- | --- |
| a key the lookup reads, or a `reads` entry | delivered | delivered |
| a parameter of the service, under `SPREAD_AUTHOR_WINS` | delivered | delivered |
| a parameter of the service, under `AUTO` or `BUNDLE` | dropped | refused: `Unexpected argument(s): 'reason'.` |
| a key nothing declares | dropped | refused: `Unexpected argument(s): 'zzz'.` |

A parameter counts when the caller could fill it by name: a keyword parameter,
or a key of an `Unpack[TypedDict]` `**kwargs`. These do not count:

- a pool seed such as `user`, `data` or `instance`, or a seed your `PoolSeeds`
  registers. The pool's value fills it, never the caller's, and the seed's name
  is never reported as an unexpected argument;
- a positional-only parameter, which dispatch never passes by name;
- a `NotClientInput` parameter, including a key a precondition or the target
  lookup marks, even where the service names it plainly.

A bare `**kwargs` declares no name. It receives the declared arguments and the
seeds the call carries (`data` and `view` among them), never an undeclared key:
under `REJECT` such a key is refused, except under `SPREAD_AUTHOR_WINS`, where
an open service drops it instead. An annotated `**kwargs` whose `TypedDict`
cannot be resolved at runtime makes `REJECT` raise `ImproperlyConfigured`.

The input schema says the same when it is told the binding. Pass
`SPREAD_AUTHOR_WINS` to `spec_to_json_schema`, and it lists the service's
parameters:

```python
spec_to_json_schema(spec, argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS)
# {"type": "object",
#  "properties": {"reason": {"type": "string"}, "notify": {"type": "boolean"}}}
```

The lookup's `pk` is not in it: a transport merges a target lookup's keys
itself, by reflecting the lookup's `SelectorSpec`. Without the argument the
schema is the bare `{"type": "object"}` it always was, which is what dispatch
admits under `AUTO`. See
[a spreading service's own parameters](../reference/jsonschema.md#a-spreading-services-own-parameters-argument_binding).

!!! note "A service with an input serializer"
    The serializer is the input, under every binding. Its validated payload
    reaches the service as `data`, and each validated value also reaches a
    parameter the service declares under the same name (the field's `source`,
    not its wire name), so a service can take `title` by name rather than
    reading `data["title"]`. A seed keeps its own value: a field named `user`,
    such as a `HiddenField(default=CurrentUserDefault())`, stays in `data`. A
    parameter named like a key any callable of the call marks `NotClientInput`
    is never handed the client's validated value either: the client's value for that field reaches the
    service only in `data`, while a view hook's, the spec's `kwargs`
    provider's or `input_data`'s value for it still reaches the parameter.

## Hiding provider-owned inputs: `NotClientInput`

Reflection advertises *every* declared key — including ones a `spec.kwargs`
provider fills in from request state, which the caller should never set:

```python
class WidgetExtras(HttpExtras[MyUser], total=False):
    project_pk: Annotated[int, InputRequired]
    team_role: Annotated[str, NotClientInput]  # resolved by spec.kwargs
```

`team_role` disappears from `properties`, and the caller's value for it never
reaches the callable. `dispatch_spec` / `adispatch_spec` treat it as no argument
the caller may send:

| The caller sends `team_role`, and the policy is... | What happens |
| --- | --- |
| `REJECT` | refused: `Unexpected argument(s): 'team_role'.` |
| `IGNORE` | the value is dropped |

The provider still fills it in. So can a view hook (`get_service_kwargs`,
`get_selector_kwargs` and their per-action forms), a registered pool seed, the
`input_data` of a service with an input serializer, the parameter's own
default, or, for a selector or a service's target lookup, a route capture of
that name (`view.kwargs`, or `build_offline_context(kwargs=...)` off HTTP);
those are channels the caller does not control. A route capture a target
lookup hides is seeded for the whole call, so a service parameter of the same
name is handed it too. For a target lookup, a registered seed of that name and the
lookup's own provider win over the capture, and an author's value for the name
(a view hook's or the spec's `kwargs` provider's) takes the capture's place.

**A key is server-owned for the whole call** once any callable in it marks it:
the selector or service, any of `spec.preconditions`, or the target lookup.
The callables of one call read the same caller input, so a key one of them
hides is gone from the declared set `REJECT` admits and from the input schema,
wherever else it is a plain parameter. A field the `input_serializer` declares
under that name is the one exception, because it is not a parameter: it stays
declared and listed, and the caller's value reaches the service only in `data`,
since a same-named field can mean something else, such as a destination tenant
beside the server's current one. The set is public as
[`server_owned_keys(spec)`](../reference/dispatch.md#server_owned_keys), for a
transport that builds an input schema of its own and must leave out the same
keys.

A hidden parameter with no default that nothing fills is not named as a missing
argument either: the caller could not send it, so the call fails as the
author's error.

`input_data` (or a `get_input_data` view hook) is the spec author's own code,
merged over the caller's input with its keys winning, the spec's over the
hook's, so its value for a hidden key is the server's and is never dropped.

!!! warning "What is left to decide the value"
    Because the caller's value is gone before anything else is read, a provider
    declining with `UNSET` cannot let it back in: what a declining provider
    leaves is the parameter's default. A caller that used to fill the key through
    its arguments, such as a task runner or a test, loses the value the same way:
    silently where the parameter has a default, and as the author's error where
    it has none, which over HTTP is a `500`. Move such a value to a provider,
    `input_data` or a pool seed. For a **scoping** key whose default reads as
    "everything", that is still a cross-scope read, so a provider owning a
    scoping key must always resolve it. A key the callable does *not* mark is
    the caller's unless the spec's `kwargs` provider is annotated to fill it:
    a key its `TypedDict` fills is never taken from the caller or a hook.

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
    nothing says which keys it has, so none is hidden and `REJECT` refuses to run
    on it (see the warning about importing the `TypedDict` at runtime, above).

### `view` is never caller input

`dispatch_spec` seeds `view` on every call: a selector, service or provider
declaring it receives the view the transport passed, or `None` off HTTP. A
caller's `view` is never an argument (under `REJECT` it is refused as
`Unexpected argument(s): 'view'.`, under `IGNORE` dropped; a caller's
`request`, and its `serializer`, which the input serializer binds, are dropped
under either, like any other seed's name), and a `kwargs` provider or a route
capture named `view` cannot outrank the seed. A spec's `kwargs` provider
returning a `view` key raises `ImproperlyConfigured`, as it does for any name
the call seeds, a view
hook's `view` is dropped for the seed, and registering `view` (or `request`) in
`pool_seeds` raises `ImproperlyConfigured`, since the edge seeds both.

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

For those, the spec declares a
[`UrlKwarg`][rest_framework_services.types.url_kwarg.UrlKwarg] in its own
`reads`:

```python
from rest_framework_services import UrlKwarg

list_widgets_spec = SelectorSpec(
    kind=SelectorKind.LIST,
    selector=list_widgets,
    reads=[UrlKwarg("project_pk", type="integer", description="Owning project.", required=True)],
)
```

Each entry is a parameter of the compiled spec, so it is an argument a caller
sends, the schema lists and `REJECT` admits (see
[what a spec declares for a caller off HTTP](../reference/service-specs.md#what-a-spec-declares-for-a-caller-off-http)).
Where the value comes from depends on the view the call carries:

- **A live DRF view** reads a declared `UrlKwarg` from its route alone, or the
  parameter's default where the route has none. A body value of that name is
  neither the argument nor written onto `view.kwargs`: the view's permission
  classes have already read the route, and a value they never saw must not
  reach the service as though the URL had carried it.
- **Off HTTP** the caller sends it as an argument. On an `OfflineServiceView`,
  which carries no route of its own, the value is also written onto
  `view.kwargs`, so a provider or a permission class reading the route finds
  it, and one the caller did not send is read from `view.kwargs`.

Route captures and query values are coerced to the type the entry declares. `required=True` is the declared
counterpart of the `InputRequired` marker: both land the name in the schema's
`required` list, and they differ only in where the key is declared. A transport
adapter that keeps its own registration and routes the value to
`build_offline_context(kwargs=...)` itself, as earlier releases needed, still
reaches a provider reading `view.kwargs`; the value is an argument only where
the spec declares or reads the name.

`QueryParam` is the sibling for read-shaping values that belong on
`request.query_params` (django-restql field selection, a serializer that branches
on the query string). It has **no** `required` flag on purpose — omitting a
read-shaping param is legitimate by construction. It follows the same split: a
live view reads it from the request's query string alone and never writes a body
value there, while off HTTP the caller sends it as an argument, which joins an
offline request's `request.query_params` when the query string lacks it.

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
selector reads *and* a scoping provider reads off `view.kwargs`. The
`reads` entry replaces the reflected parameter of the same name. Over HTTP both
readers see the route's value; off HTTP `dispatch_spec` delivers the caller's
argument to the selector and writes it onto the `OfflineServiceView`'s
`view.kwargs`, so both readers see it there too.

## Who validates what

One spec, two validators, chosen by the view the call carries:

| The call carries... | Validated by | So |
| --- | --- | --- |
| a live DRF view (any view that is not an `OfflineServiceView`) | the input serializer alone, from the body the request parsed | DRF's coercion and wording: `"true"`, `1` and `"on"` for a boolean, a number for a string, form bodies, every field's refusal at once |
| an `OfflineServiceView`, or no view | the kernel's typed check of every declared argument, then the serializer | JSON types only: `"true"` for a boolean or `123` for a string is refused, a Python `date` or `Decimal` is refused, a list past 1000 items is refused, and the serializer runs only once every argument passes, so its own refusals (a `max_length`, a `validate()`) wait behind a type refusal |

An agent transport sends typed JSON and wants a refusal it can show a model, so
it calls `dispatch_spec` with an `OfflineServiceView` (from
`build_offline_context`) or none. A browser or a form sends what DRF has always
accepted, so the views keep answering it as DRF does. A `many` call's list of
rows is bounded off HTTP and on an `OfflineServiceView` only: 1000 rows unless
the `ListSerializer` sets `max_length` or the project sets
`SERVICE_SPECS_MAX_ARRAY_LENGTH`. Over a live view the `ListSerializer` alone
bounds a bulk body.

[pep692]: https://peps.python.org/pep-0692/
