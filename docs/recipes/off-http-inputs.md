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

A name the target lookup's selector reads that is also a writable input field,
such as a lookup by `slug` on a spec that renames the `slug`, is the lookup's
from the capture alone and the field's from the params: the capture finds the
row and the params' value is validated and written. With no capture the params'
value never scopes the lookup, which keeps its own default, or is refused as
missing where it has none. A view hook's or `kwargs` provider's value for the
name does both.

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
    argument wherever the call reads it, so an unresolvable one raises
    `ImproperlyConfigured` there rather than quietly letting every argument
    through. The call reads it on a selector, a top-level one or a target
    lookup's, under every `ArgumentBinding`, and on a service with no
    `input_serializer` under `SPREAD_AUTHOR_WINS` alone, where its parameters are
    the caller's input. It does not raise on a service dispatched under `AUTO` or
    `BUNDLE`, which reads none of its signature, nor on a service with an input
    serializer, whose serializer declares the input, nor beside a target lookup
    whose selector takes a bare `**kwargs`, which leaves the call open: those
    calls run. The permissive policies read an unresolvable annotation as an open
    `**kwargs`, as they always have.
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

Dispatch enforces that one without a marker too, once the call's providers,
view hooks and pool seeds have run, since any of them may still fill it. A call
that lacks it then is refused the same way, `{"pk": ["This field is
required."]}`, with every such name keyed in the one refusal. A name the
declaration itself requires (one marked `InputRequired`, a required declared
read, a required input serializer field) is refused earlier, before the
permission checks run, so a call missing names of both kinds is answered in two
refusals, the declared names first. A spreading service's target lookup key is
asked for as the service is called, after both. That holds for the
target lookup, the selector under `AUTO` or `SPREAD_AUTHOR_WINS` (under
`BUNDLE`, which a selector view dispatches, only its declared `reads` are caller
input, and a parameter nothing fills is the selector's own `TypeError`), and a
service with no `input_serializer` dispatched
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

A target lookup that shapes its rows with a `filter_set` declares the filter's
names and its selector's parameters, so under `AUTO` or `BUNDLE` a key outside
them is refused under `REJECT` there too; a lookup whose selector takes a bare
`**kwargs` declares none, and nothing is refused. Under `SPREAD_AUTHOR_WINS` a
serializer-less service's own parameters join the set: a key that is one of them
is delivered, any other is refused, and a service taking only a bare `**kwargs`
declares none and drops the key, as below. A service with an `input_serializer`
joins nothing to the set, so there a key outside the lookup's names and the
serializer's fields is refused under `REJECT` whether the signature is closed,
takes a bare `**extras` or names the key as a parameter.

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
a serializer-less open service drops it instead; one with an `input_serializer`
still refuses it. An annotated `**kwargs` whose `TypedDict`
cannot be resolved at runtime makes `REJECT` raise `ImproperlyConfigured` where
the call reads that annotation, as the warning above sets out: for this service,
under `SPREAD_AUTHOR_WINS`, unless its target lookup's selector takes a bare
`**kwargs`.

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
    provider's or `input_data`'s value for it still reaches the parameter,
    as the field validates it.

    On a DRF view such a parameter needs a default (`title=None`), or the
    service reads `data["title"]`: `as_view()` checks a service's required
    parameters before any call, against the keys every call provides, and a
    field's value is not one of them, since a `PATCH` may leave it out. A
    required parameter of a field's name is refused with
    `ImproperlyConfigured` when the view is built; `dispatch_spec` hands it
    the validated value. A spec with a `kwargs` provider, or a view
    overriding a `get_*_kwargs` hook, is not checked there, since its keys
    cannot be known before a call.

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
(a view hook's or an untyped `kwargs` provider's) takes the capture's place. A
spec whose typed `kwargs` provider is annotated to fill a name the lookup reads
is refused with `ImproperlyConfigured`, since that provider's values reach the
service only after the target is resolved: make it untyped, or move the key to
the lookup selector spec's own `kwargs`.

**A key is server-owned for the whole call** once any callable in it marks it:
the selector or service, any of `spec.preconditions`, or the target lookup.
The callables of one call read the same caller input, so a key one of them
hides is gone from the declared set `REJECT` admits and from the input schema,
wherever else it is a plain parameter. A field the `input_serializer` declares
under that name is the one exception, because it is not a parameter: it stays
declared and listed, and the caller's value reaches the service only in `data`,
since a same-named field can mean something else, such as a destination tenant
beside the server's current one. A filter of the selector's `filter_set` under
that name is the other: the filter reads the caller's value, as every filter
does, and the parameter is handed only the server's (the provider's, a view
hook's or a route capture's) or its own default, so neither ever reads the
other's. The set is public as
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
    a key its `TypedDict` fills is never taken from the caller or a hook,
    declined or not, nor through a field whose `source` it is. A key that is a
    field's wire name owns that field's `source` as well, so with
    `tenant = CharField(source="org")` a provider filling `tenant` leaves the
    caller's value out of both the `tenant` and the `org` parameters, and a
    hook naming either is dropped. A `NotClientInput` mark on `tenant` keeps
    the caller's value out of `org` the same way. An input field
    it names stays the serializer's: the caller's value is validated and
    reaches the service in `data` alone, as a `NotClientInput` key's does,
    while an `input_data` value for that field is the server's and reaches
    the parameter.

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
    on it wherever the call reads that annotation (see the warning about
    importing the `TypedDict` at runtime, above, for where that is).

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
sends, the schema lists (bar a `QueryParam` that is a shape, which
`spec_to_json_schema` does not list) and `REJECT` admits (see
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

A target lookup's `reads` take the same channels the same way, so a
`SelectorSpec(reads=[QueryParam("q")])` used as an `instance_selector_spec` reads
`q` from a live view's query string alone, and each is coerced to the type it
declares, as the spec's own are. A name the spec's `reads` declare too is read
by the spec's declaration. A read named like a writable field of
the input serializer is refused when the spec compiles, because the body's value
of that name is the field's, except a `UrlKwarg` the target lookup reads, which
renames a row by the key that finds it: the route's capture scopes the lookup,
and off HTTP the caller's value for the name is the field's alone, never written
onto `view.kwargs`.

A service parameter named like a read is handed its value by name. On a DRF
view it needs a default, as one named like an input field does: `as_view()`
checks a service's required parameters before any call, and a request may
carry no query value, so a required parameter of a read's name is refused
when the view is built. Off HTTP, `dispatch_spec` hands it the argument. A
selector's parameter is not refused: a selector takes its arguments from its
signature, so a required one makes the read a required argument, and a request
without it is answered `400`.

The [example project](https://github.com/Artui/djangorestframework-services/tree/main/examples) declares `reads=[QueryParam("status")]` on its list
spec and asserts the selector is handed it both ways, beside an off-HTTP
`dispatch_spec(partial=True)` call that validates only the fields it sends.

Route captures and query values are coerced to the type the entry declares. `required=True` is the declared
counterpart of the `InputRequired` marker: both land the name in the schema's
`required` list, and they differ only in where the key is declared. A read of
the name of a parameter that has no default, of the selector, a target lookup,
the service or a precondition, is required without `required=True`, since the
callable cannot run otherwise; only a `default` on the read, or on the
parameter, makes it optional in the compiled parameters and the kernel's input
schema (`spec_input_schema`), which report a default and do not fill it in. A
parameter marked `InputRequired` stays required under a read even with a `None`
default. A transport
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

A `QueryParam` that no callable of the call takes by name is a **shape**: it
shapes what is rendered rather than what is run, so the kernel hands it to the
presenter alone ([which callables count](../reference/types.md#queryparam)).
When the kernel's `dispatch` presents the result, the presenter builds an
offline request for the principal whose query string is the call's shapes, so a
serializer reading `fields` off `request.query_params` finds what the caller
sent without anything being put on a request by hand:

```python
from django_service_specs import dispatch, kernel_spec, present
from rest_framework.permissions import AllowAny


class AuthorSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()

    def to_representation(self, instance):
        data = super().to_representation(instance)
        wanted = self.context["request"].query_params.get("fields")
        if wanted:
            data = {name: data[name] for name in wanted.split(",") if name in data}
        return data


authors = SelectorSpec(
    kind=SelectorKind.LIST,
    selector=list_authors,  # takes no `fields`, so it is a shape
    output_serializer=AuthorSerializer,
    permission_classes=[AllowAny],
    reads=[QueryParam("fields", description="Comma-separated fields to return.")],
)

spec = kernel_spec(authors)
result = dispatch(spec, principal=user, arguments={"fields": "name"})
present(spec, result)  # [{"name": "Ada"}]
```

The drfs edge renders in a separate step, with the `request` and `view` the
caller hands `render_spec_output`, so there the query string is the one the
request carries: the offline request `build_offline_context(query_params=)`
seeds, and onto which `dispatch_spec` puts a declared `QueryParam` the string
lacks when the view is an offline one. A DRF `Request` the call carries is left
as it is on the kernel's route: it has a query string of its own.

Both types live here rather than in each adapter so the same declaration means the
same thing on every transport. Adapters validate a registration set with
[`validate_channel_names`][rest_framework_services.types.validate_channel_names.validate_channel_names], which
always includes `RESERVED_POOL_SEEDS` and takes the transport's own reserved
pagination names on top.

## The request and view a call carries

Over HTTP the permission classes and the serializers meet the request and view
the framework built. Off HTTP the call may carry them too, in either of two
places, and each consumer reads them in the same order: the `request` or `view`
the call registered as a seed, else the entry of that name in its
`call_context`, with `None` in either place counting as no value. A seed outranks
the context, so a transport's own registered object is never replaced by what a
caller put in the dict. `dispatch_spec` registers the `request` and `view` it
was handed, so one passed there is the seed and wins; with none passed, an entry
the caller put in `call_context` is read. A transport that runs the kernel's
`dispatch` over `kernel_spec(spec)` registers none of drfs' edge seeds, and its context
holds the request a run arrived on and, perhaps, a view built for the operation
([the call's context](../reference/dispatch.md#transport-neutral-dispatch)). A seed
it does register fills a selector's, a target lookup's or a spread service's
signature parameter of that name, since the compile marks every such read
`reflected`, while a declared read of that name is still refused.

What a carried request or view becomes differs by consumer, and by what it is:

| The call carries... | `DRFPermissionCheck` asks the classes | The serializer context holds |
| --- | --- | --- |
| a DRF `Request` and a view | both, as they are | both, as they are |
| a DRF `Request` alone | the request, beside an offline view | the request, beside `None` for the view |
| a view alone | the view, beside an offline request for the principal | the view, beside `None` for the request |
| a Django `HttpRequest` DRF has not wrapped | a DRF `Request` around a copy of it, beside the carried view, else an offline view | the same wrapped request, beside the carried view, else an offline view built around it |
| neither | an offline request and view for the principal | an offline request and view for the principal |

A permission class is never handed `None` for a request or a view, which is why
it fills the half the call lacks. A serializer is not asked to, and pairing a
carried request with a view built around another one would describe a call that
never happened, so the serializer context keeps one carried half as it is. A
view that answers `get_serializer_context` supplies the serializer context
whole, as it does on HTTP.

A carried Django request is wrapped as `build_offline_context(http_request=)`
wraps one: a copy of it, so the request the run arrived on is never written to,
with its headers and session shared, its `user` the principal, and its query
string replaced. The query string is the arrived-on endpoint's, not this
operation's, so it is not kept. The serializer's is the call's shapes, and
empty where the call sent none. A permission class's is the value of each
declared `QueryParam` the call sent, or its `default`, with the request's own
left alone only when the permission check is asked with no arguments at all.

The offline view's `action` is the call context's `action`, where that is a
non-empty string, so a permission class or a serializer branching on
`view.action` reads the operation's name rather than `None`. A view the call
carries keeps its own `action`.

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
