# Dispatch (stable surface)

The **stable dispatch surface**: the primitives an alternate transport
(such as [djangorestframework-mcp-server](https://github.com/Artui/djangorestframework-mcp-server))
builds on instead of re-implementing the "how to call a service / selector"
rules. Every symbol here is importable from the top-level package, follows
semantic versioning, and will not move or change signature within a major
version. Blessed in 0.17, which also **removed the private `_compat`
package** — `run_service` / `arun_service` now live in `services/` and
`is_async` at the package root; downstreams re-point their imports here
when they bump past 0.17.

Deliberately **not** part of this surface: `dispatch_mutation_for_spec` and
`dispatch_selector_for_spec`. They are view-coupled orchestrators (they take
a view, read its URL kwargs, and walk the `get_<action>_*_kwargs` hook
chains). The transport-neutral spec dispatcher that composes the leaves
below — `dispatch_spec` — is documented first.

`dispatch_spec` and `adispatch_spec` are the DRF edge over
[django-service-specs](https://artui.github.io/django-service-specs/)'
`dispatch` and `adispatch`: they compile the spec to the kernel's
([Service specs on the kernel](service-specs.md)), hand it the call, and
translate what it refuses into DRF's exceptions. The order of a call, the
closed argument set, validation, affordances, preconditions, the run and the
output re-read are the kernel's, documented on its `dispatch`.

Most leaves on this page are the kernel's own too, re-exported at the path this
package has always exported them from: `DispatchResult`, `UnknownArguments`,
`OutputPage`, `paginate_output`, `DEFAULT_PAGE_SIZE`, `enforce_affordances`,
`aenforce_affordances`, `unmet_operation_affordance`,
`aunmet_operation_affordance`, `operation_affordances`, `provider_keys`,
`ProviderKeys`, `is_async`, `run_service`, `arun_service`, `run_selector`,
`arun_selector`, `is_queryset`, `materialize_retrieve`,
`amaterialize_retrieve`, `base_pool` and `null_progress`. Each is the same
object under both names, so the import you have keeps working and the
documentation rendered for it here is the kernel's.

## Transport-neutral dispatch

The single execution path a non-HTTP transport drives: hand a spec, the
acting `user`, and a flat `params` mapping, get back a `DispatchResult` to
format for your wire. No view, no `request` required.

Each primitive comes as a sync / async pair — `dispatch_spec` /
`adispatch_spec`, `render_spec_output` / `arender_spec_output`. The async
member is not a thin alias: a spec's callables (selector, service, `kwargs`
provider, `extend_queryset`, `filter_set`, serializer-context providers) are
written once for both transports and so are never `async def`, and rendering
walks the ORM. The async members run all of that in Django's thread-sensitive
executor, which is what keeps `SynchronousOnlyOperation` out of an async
transport. Await the pair together; don't mix an `a`-prefixed call with a sync
one on the same result.

**Who validates a call depends on its `view`.** A live DRF view, meaning any
`view` that is not an
[`OfflineServiceView`][rest_framework_services.types.offline_service_view.OfflineServiceView],
has its request validated by the input serializer alone, from the body the
request parsed, in DRF's words. With an `OfflineServiceView` or no `view`, the
kernel first checks every declared argument against its type, so `"true"` for a
boolean or `123` for a string is refused before the serializer runs. A declared
`UrlKwarg` or `QueryParam` follows the same split: a live view reads it from its
route or query string alone, and off HTTP it is the caller's argument. See
[who validates what](../recipes/off-http-inputs.md#who-validates-what).

### `dispatch_spec`

::: rest_framework_services.dispatch.dispatch_spec.dispatch_spec

### `adispatch_spec`

::: rest_framework_services.dispatch.adispatch_spec.adispatch_spec

### `render_spec_output`

::: rest_framework_services.dispatch.render_spec_output.render_spec_output

### `arender_spec_output`

::: rest_framework_services.dispatch.arender_spec_output.arender_spec_output

### `render_for_audience`

The agent-audience twin of `render_spec_output`: it renders through the same
path, then applies the serializer's
[`FieldMarking`](types.md#fieldmarking) markings. An alternate transport whose
consumer is a model calls this instead, so every such transport shapes payloads
identically. Not for a pipeline that feeds one spec's output into the next —
that still wants `render_spec_output`, or the handles the next step reads by
will have been projected away.

::: rest_framework_services.dispatch.render_for_audience.render_for_audience

### `arender_for_audience`

::: rest_framework_services.dispatch.arender_for_audience.arender_for_audience

### `paginate_output`

One page of a list selector's rows, in the envelope
[`output_to_json_schema`](jsonschema.md#output_to_json_schema) has always
published for `kind=LIST, paginate=True` — `{items, page, totalPages, hasNext}`.
That schema described a payload nothing in this package produced: the shaper
lived in a transport, so one agent transport wrapped its pages and another
returned a bare list, against one schema claiming the envelope for both.

Rendering happens between the two calls, because it needs a view, a request and
a spec that belong to the caller:

```python
page = paginate_output(rows, page=page, limit=limit, max_page_size=ceiling)
rendered = render_for_audience(spec, page.items, projection=projection, many=True, ...)
payload = page.envelope(rendered)
```

`page` and `limit` are taken already parsed. Turning an untyped argument into an
integer is where transports legitimately differ — a public endpoint clamps a
malformed value and answers, an in-process toolset can hand the model its
mistake back and ask again — and that is a policy about bad input, not about
what a page is. Everything that *is* about what a page is lives here: the clamps
at both ends, the count taken before the slice, and the reported `page` being
the one actually served.

`rows` is a queryset, a manager or a sized, sliceable sequence. A queryset is
counted with `COUNT` and sliced lazily; a manager — `Model.objects`, which a
LIST selector may return as it is — is paged as its `.all()`; a `list` or
`tuple` is paged in memory. Anything else raises `TypeError`, since there is
nothing to count and nothing to slice.

::: rest_framework_services.dispatch.paginate_output.paginate_output

### `DEFAULT_PAGE_SIZE`

::: rest_framework_services.dispatch.paginate_output.DEFAULT_PAGE_SIZE

### `OutputPage`

::: rest_framework_services.types.output_page.OutputPage

### `base_serializer_context`

The DRF baseline (`request` / `format` / `view`) that every serializer gets for
free over HTTP, synthesized for the off-HTTP path. `dispatch_spec` and
`render_spec_output` apply it themselves — reach for it directly only when a
transport builds a serializer outside them. See
[Customise serializer context](../recipes/serializer-context.md#off-the-http-path).

::: rest_framework_services.dispatch.base_serializer_context.base_serializer_context

### `renderable_serializer_class`

The class an `output_serializer` declaration renders through: a serializer
class as declared, a bare dataclass wrapped in a `DataclassSerializer`.
`render_spec_output` and the HTTP views resolve it themselves — a transport
that instantiates an output declaration outside them resolves it here first,
or a dataclass output raises where a serializer would have rendered.

::: rest_framework_services.dispatch.renderable_serializer_class.renderable_serializer_class

### `DispatchResult`

::: rest_framework_services.types.dispatch_result.DispatchResult

It carries no `status`: a transport answers its own, which over HTTP is the
view's `success_status`. `dispatch_spec` still accepts `success_status=` for
the transports that pass it, and ignores it. `context`, new with the kernel,
is the call a presenter renders for, which the output serializer's context is
built from.

A `ServiceSpec`'s `kind` is `"list"` for a `many=True` spec, and otherwise
whenever its `output_selector_spec` declares `kind=SelectorKind.LIST`, with a
`selector` to re-read through or without one. Without one, the `value` is the
service's own return, passed through as it came: a `QuerySet` stays lazy, as a
re-read one does, and a list or a generator is the same object. A return that is no set
of rows (a mapping, a `str` or `bytes`, `None`, anything that does not iterate)
raises `ImproperlyConfigured` naming the declaration and the type returned,
after the service has written; see
[what a service's output schema describes](jsonschema.md#what-a-services-output-schema-describes),
which states the same array.

## Input policies

Three optional, caller-side policies let a transport map its wire onto a spec
without `dispatch_spec` baking in HTTP's implicit answers. The **spec declares
*what*** (its inputs, filters, output shape, permissions); the **caller declares
*how*** strict to be about undeclared keys and how to authorize a resolved
target.

### `ArgumentBinding`

A call has **one argument pool**: every callable takes its keyword arguments
from it by name, and an author's value (a view hook's, or the spec's `kwargs`
provider's) outranks the caller's for the same name. What the enum still
decides is one question, about a `ServiceSpec` with no `input_serializer` and
about a `SelectorSpec`: whether the callable's own parameters are caller input.

- `AUTO`, the default, is `BUNDLE` for a service and `SPREAD_AUTHOR_WINS` for a
  selector. A selector's declared `reads` are the caller's under every member.
- Under `BUNDLE` a selector's own parameters are not the caller's either: a
  route capture, an author's value, a seed and its declared `reads` fill them,
  its `filter_set` still reads the caller's values, and a parameter nothing
  fills is the callable's own `TypeError` rather than a `400` no client could
  answer. A selector view dispatches `BUNDLE`.
- Under `BUNDLE` a serializer-less service's own parameters are never the
  caller's: it receives `data={}`, its declared `reads` and the target lookup's
  row, and nothing else the client sends.
- Under `SPREAD_AUTHOR_WINS` its signature is the caller's input, still below an
  author's value on a conflict.
- `SPREAD_CALLER_WINS` is refused with `ValueError` naming `AUTO`: leave a key
  out of the `kwargs` provider where the caller's value should win.

A service with an `input_serializer` reads the serializer under every member:
the validated payload arrives as `data`, and each validated value also reaches a
parameter of the same name (the field's `source`). A parameter named like a key
any callable of the call marks `NotClientInput` is never handed the client's
validated value: the client's
value reaches it only through `data`, while an author's value for it (a view
hook's, the spec's `kwargs` provider's or `input_data`'s) still reaches the
parameter. On a DRF view such a parameter needs a default, since `as_view()`
refuses a required parameter no call is sure to provide. See
[a service with no input serializer](../recipes/off-http-inputs.md#a-service-with-no-input-serializer).

::: rest_framework_services.types.argument_binding.ArgumentBinding

### `UnknownArguments`

How strict `dispatch_spec` is about `params` keys outside the spec's declared
set: drop them (`IGNORE`, `dispatch_spec`'s default) or refuse them (`REJECT`,
the kernel's default). `PASSTHROUGH` stays importable, as the kernel's member,
but passed to `dispatch_spec` it is refused with `ValueError`: an argument the
spec does not declare never reaches a callable. Declare it instead, as a `reads`
entry, a serializer field or a parameter.

The declared set is the compiled spec's parameters
([Service specs on the kernel](service-specs.md#where-each-field-lands)): the
`input_serializer`'s fields, or, for a service with none dispatched under
`SPREAD_AUTHOR_WINS`, its own signature; a
selector's signature, less the names its `filter_set` owns, which are the
filter's; the target lookup's; and every `reads` entry. A key that any callable
in the call marks `NotClientInput`, and every pool seed's name, is
server-owned and never declared. `REJECT` names every undeclared key in one
`"Unexpected argument(s): ..."` message under `non_field_errors`, a row's
inside that row; a pool seed's name is never one, so a caller's `request` is
dropped like any seed's, while a caller's `view` is refused. Off HTTP an unknown key inside a nested serializer is refused in
the kernel's words, `{"inner": {"zzz": ["Unknown argument."]}}`; over a live
view the nested serializer drops it, as DRF does. On DRF 3.18 or later a live
view's `many` call can answer in two shapes: rows the serializer accepted but
which carry an undeclared key are refused as a list of rows, while a row the
serializer refuses comes back in DRF's mapping of row indexes. Where a target lookup
will query, an undeclared key is refused before it does. A selector or target
lookup taking a bare `**kwargs`, and a service with no `input_serializer` taking
one under `SPREAD_AUTHOR_WINS`, drop an undeclared key rather than refuse it.

::: rest_framework_services.types.unknown_arguments.UnknownArguments

### `TargetGuard`

The object-permission hook invoked with the resolved mutation target before the
service runs. Its signature matches `enforce_permissions`, so that primitive is
passed directly — by name, not wrapped in a `lambda`.

::: rest_framework_services.types.target_guard.TargetGuard

### `ViewHooks`

The calling DRF view's resolved hook-chain layers, handed to the core so one
pipeline serves both transports. **HTTP-only** — like `view`, an off-HTTP caller
omits it and nothing changes. It carries the *view* layers only
(`get_service_kwargs`, `get_input_data`, the serializer-context hooks and their
per-action twins); the spec's own `kwargs` / `input_data` /
`input_serializer_context` providers stay the core's job, so they resolve exactly
once no matter who dispatches.

The spec's `kwargs` provider outranks the view's hooks. A hook value for a name
the call reads (a parameter of the spec's callable or target lookup, or a field
of the input serializer, by its name or its `source`) is that argument's value,
over the client's, and is validated as the client's would be. For a field, the
value given is validated on its own, by the field and its `validate_<field>`,
once the serializer has accepted the body, never what the field reads back from
the body; what that gives is the value under its `source` key, and under the key
the hook used where that differs, wherever the call
reads it (the service's parameter, `data`, the bound serializer's
`validated_data`), even where a later field with the same `source`, a
`source="*"` nested serializer, the serializer's `validate()` or a
`to_internal_value` override writes that key from the client's input, or runs
the field's validation again on a value of its own; `input_data` is held the
same way. That run is the field's second for the call, so a related field
queries once more for each key it holds and the field's validators and its `validate_<field>` run
twice, as do a nested serializer's `validate()` and its fields' own. A field with a dotted `source` or
`source="*"` hands it to the parameter named like the field. Where `data` is a
dataclass instance it is left as the serializer built it, and so are a `many`
spec's rows (see [extra kwargs](../recipes/extra-kwargs.md)). A value naming a
seed (`request`, `view`, `progress`) is dropped, the seed winning; anything else
reaches the callables beside the arguments. `input_data` from the hooks is
merged beneath the spec's own `input_data`.

::: rest_framework_services.types.view_hooks.ViewHooks

## Authorizing an off-HTTP call

`dispatch_spec` is authz-agnostic by design — it never consults a spec's
`permission_classes` (on HTTP that is the view's job). An off-HTTP transport
that wants the *same* authorization a DRF view would apply wires
`enforce_permissions` in **two** places:

```python
from rest_framework_services import (
    adispatch_spec,
    build_offline_context,
    enforce_permissions,
)

context = build_offline_context(user)
# 1. Class-level `has_permission`, before any work. Covers create / list-payload
#    and every spec that has no resolvable target.
enforce_permissions(spec, context)
# 2. Object-level `has_object_permission`, on the resolved target. Fires on the
#    mutation *and selector* paths (update, retrieve, and — class-level only —
#    bulk / list collections, which are not per-row authorized).
result = await adispatch_spec(
    spec,
    user=user,
    params=params,
    request=context.request,
    view=context.view,
    on_target_resolved=enforce_permissions,
)
```

The upfront call is what authorizes a spec with **no** target (a create, or a
`many=True` list-payload); the `on_target_resolved` hook adds object-level checks
once the row (or collection) is resolved. Together they are the canonical wiring
for every spec kind. `enforce_permissions` is collection-safe: a resolved
queryset runs only the class-level check, never `has_object_permission`.

### `build_offline_context`

::: rest_framework_services.dispatch.build_offline_context.build_offline_context

**Read-shaping over the offline path.** Pass `query_params=` to seed the synthetic
request's `GET` `QueryDict` — the source `request.query_params` reads. That is how
read-shaping params that are *not* spec inputs reach the serializer off-HTTP: any
serializer that branches on `request.query_params` (django-restql field selection,
custom serializers), and a request-scoped `FilterSet` that reads
`self.request.query_params` itself. It is not what a `filter_set` is bound to:
`dispatch_spec` binds a `SelectorSpec`'s own `filter_set`, and a target lookup's,
to `filter_data` when it is passed, else to `params`, reading only the filter's
own names from either. `filter_data`, and the query string a live view's filter
reads, feed the filter alone: the selector, its preconditions and a target
lookup's selector never receive them, unless a declared `QueryParam` names the
filter. Under a filter's name those callables take the caller's `params` value as
sent, a route capture, or an author's value. A service's `output_selector_spec.filter_set` is handed
no arguments, so neither narrows the re-read. To filter from the synthetic query
string, pass `filter_data=context.request.query_params`. It does **not** make DRF
`filter_backends` (`SearchFilter` / `OrderingFilter`) run — the offline path never
calls `filter_queryset`; `filter_set` is the drf-services-native equivalent.

### `OfflineHttpRequest`

The synthetic request `build_offline_context` creates when there is no ambient
one. Relevant when you're deciding what `build_absolute_uri()` should return off
the HTTP path — see
[Absolute URLs off the HTTP path](../recipes/serializer-context.md#absolute-urls-off-the-http-path).

::: rest_framework_services.types.offline_http_request.OfflineHttpRequest

### `enforce_permissions`

::: rest_framework_services.dispatch.enforce_permissions.enforce_permissions

### `enforce_affordances` / `aenforce_affordances`

`dispatch_spec` checks a spec's `affordances` itself. A transport that runs a
service **without** the core -- a chain step handed a pool it built, say -- calls
one of these the way it calls `enforce_permissions`: after permissions and target
resolution, before the spec's `preconditions`, which is the order the core uses.
Skip it and a call the direct path refuses with `ActionUnavailable` succeeds on
that path instead. Like the two below, they are the kernel's functions with
`request` and `view` reserved beside the `reserved` set passed, the names
`dispatch_spec` seeds itself, so a condition reading either is handed it from
the pool.

::: rest_framework_services.dispatch.enforce_affordances.enforce_affordances

::: rest_framework_services.dispatch.aenforce_affordances.aenforce_affordances

### `unmet_operation_affordance` / `aunmet_operation_affordance`

A transport deciding which operations to offer, such as an MCP server building
its tool list or an agent assembling its toolset for a step, asks one of these
before any call is attempted. They answer the conditions the capability manifest
calls `"operation"` scope, the callables that need no row, and return the first
one not met, or `None`. A condition on the row is skipped without a query. Build
the pool with `base_pool` and the same `seeds=` the call would use, passing the
`request` (and `view`, where there is one) the call would be handed, and pass
`reserved=seeds.reserved`. `dispatch_spec` seeds `request` and `view` itself on
every call, beside the seeds it is handed, so these functions reserve both beside
the set you pass: a condition declaring either is answered from the pool here as
it is at the call, and the two agree. The answer is advisory: `dispatch_spec`
still enforces every affordance when the operation is called.

::: rest_framework_services.dispatch.unmet_operation_affordance.unmet_operation_affordance

::: rest_framework_services.dispatch.aunmet_operation_affordance.aunmet_operation_affordance

### `operation_affordances`

The conditions those two would ask, before anything is asked. An empty answer
means there is nothing to ask at list time, so a transport can skip building a
pool, or making a thread hop of its own, for most of the operations it lists.

::: rest_framework_services.dispatch.operation_affordances.operation_affordances

## Shared

### `resolve_callable_kwargs`

::: rest_framework_services.views.utils.resolve_callable_kwargs

### `provider_keys`

The static half of the `kwargs=` provider contract, whose runtime half is
dispatch calling the provider and dropping every key it returns as `UNSET`. A
transport building a selector's input schema reads it to fill in
[`supplied`](jsonschema.md#what-a-transport-supplies-supplied), and to know
which calls to refuse before dispatch, rather than keeping a copy of the
reader:

```python
from rest_framework_services import provider_keys

keys = provider_keys(spec.kwargs)
if keys is None:
    ...  # The provider may fill any name, so none is required for lacking a default.
else:
    filled, declinable = keys
    # ``filled`` joins ``supplied``. A ``declinable`` key stays the caller's to
    # send and is not required of it, since the provider may fill it after all.
```

The answer is a [`ProviderKeys`](#providerkeys), a `NamedTuple`, so a caller
that would rather not unpack it reads `keys.filled` and `keys.declinable`.

::: rest_framework_services.dispatch.provider_keys.provider_keys

### `ProviderKeys`

::: rest_framework_services.types.provider_keys.ProviderKeys

### `server_owned_keys`

The names no caller supplies anywhere in a spec's dispatch: every key the
selector or service, one of `spec.preconditions` or the target lookup marks
[`NotClientInput`](types.md#notclientinput), and `view`. Dispatch drops the
caller's value for each before any callable reads it, `REJECT` refuses it on a
closed spec, and `spec_to_json_schema` leaves it out of the input schema.

The set governs the keyword pool, not an input serializer's fields. A field the
`input_serializer` declares under an owned name stays client input: the schema
lists it, `REJECT` admits it, and the caller's value is validated into `data`,
the one place it reaches the service, since dispatch keeps it out of the spread.
A same-named key can mean something else there, such as a destination tenant in
`data` beside the server's current tenant at the gate, so the declaration is not
refused.

A transport that builds an input schema of its own subtracts the same set, less
the input serializer's field names, rather than reading each callable's markers
again, which would miss a key that one callable hides and another takes plainly:

```python
from rest_framework_services import server_owned_keys

# A service tool merging its target lookup's keys into what it built from the
# service's input serializer (empty when the spec has none).
properties = {**lookup_properties, **serializer_properties}
# A field the serializer declares stays the caller's, whatever its name.
owned = server_owned_keys(spec) - set(serializer_properties)
for name in owned:  # the service spec, not its nested lookup
    properties.pop(name, None)
```

Pass the spec the transport dispatches. Asked with the nested
`instance_selector_spec` / `collection_selector_spec`, it answers only what the
lookup and its own preconditions hide, and a key the service or one of its
preconditions hides is still advertised, then dropped.

::: rest_framework_services.dispatch.utils.server_owned_keys

### `is_async`

::: rest_framework_services.is_async.is_async

## Service side

### `run_service`

::: rest_framework_services.services.run_service.run_service

### `arun_service`

::: rest_framework_services.services.arun_service.arun_service

### `build_input_serializer`

::: rest_framework_services.views.mutation.utils.build_input_serializer

### `build_input_serializer_from_data`

::: rest_framework_services.views.mutation.utils.build_input_serializer_from_data

### `validate_input`

::: rest_framework_services.views.mutation.utils.validate_input

### `resolve_mutation_instance`

::: rest_framework_services.views.mutation.utils.resolve_mutation_instance

## Selector side

### `run_selector`

::: rest_framework_services.selectors.utils.run_selector

### `arun_selector`

::: rest_framework_services.selectors.utils.arun_selector

### `is_queryset`

::: rest_framework_services.selectors.utils.is_queryset

### `materialize_retrieve`

::: rest_framework_services.selectors.utils.materialize_retrieve

### `amaterialize_retrieve`

::: rest_framework_services.selectors.utils.amaterialize_retrieve

### `apply_queryset_shaping`

drfs' signature over the kernel's `shape_queryset`, for a caller holding a view
and a request rather than a spec. Two things differ from the releases before
the kernel:

- **`filter_set` must be a django-filter `FilterSet`.** One duck-typed on
  `(data, queryset) -> .qs` is refused with `ImproperlyConfigured`. Each value
  is coerced to the type its filter declares, and a repeated query-string key
  reaches a filter taking several values as a list.
- **Invalid filter data raises the kernel's `InvalidArguments`**
  (`django_service_specs.parameters.invalid_arguments`), keyed as the
  `FilterSet` keys its errors, and nothing here translates it. It is not a DRF
  exception, so a caller inside a DRF view that wants a 400 translates it itself,
  with
  [`translate_dispatch_errors`](service-specs.md#translate_dispatch_errors).
  `dispatch_spec`, and every view that dispatches through it, shapes inside the
  kernel's `dispatch` and translates for you, answering 400.

`extend_queryset` is still called as `extend_queryset(qs, view, request)`, by
position; one answering with an awaitable is refused.

::: rest_framework_services.selectors.utils.apply_queryset_shaping

## Pool seeds

Every kwargs pool the kernel builds for a dispatched spec starts from
`base_pool`, whether `dispatch_spec` was called by an HTTP view or by a
transport off HTTP, so a spec callable that declares a seed behaves the same
over either. `request` is not one of its parameters: `dispatch_spec` registers
`request` and `view` as seeds of each call, so a project registering either in
`pool_seeds` is refused with `ImproperlyConfigured`. A falsy progress reporter
is kept rather than swapped for `null_progress`.

A parameter named like a seed the call registers is the seed's, on a selector,
a target lookup or a spread service as on a service: the seed fills it, and a
caller's value of the name is dropped rather than reaching it. `dispatch_spec`
compiles that per call, from the `pool_seeds` it is handed. `kernel_spec(spec)`
knows no project's seeds, so the kernel's own `dispatch`, handed the same seeds
for that compiled spec, refuses the parameter as an argument meeting a seed. A
name declared in the spec's `reads` is refused by both.

`base_pool` is a builder, not a gate, and nothing checks a pool assembled
elsewhere. **If you are writing a transport adapter, build your pool by calling
it** — `base_pool(user=…, **your_own_entries)` — rather than writing a
dict literal of your own:

- A literal that omits a seed carries no trace of it. `resolve_callable_kwargs`
  forwards only the keys the pool has, so a callable declaring the missing seed
  raises a `TypeError` at call time: the same spec works when your adapter
  dispatches it one way and fails another. `progress` is the usual casualty —
  it is the seed with a default, so a callable can declare it in the
  documented `progress: ProgressReporter` form and never see that anything is
  missing until a pool without it comes along.
- Spreading your own entries through `**extra` makes a name collision loud. An
  entry called `user` raises `TypeError` at the `base_pool` call;
  in a dict literal the same entry silently outranks the value your transport
  authenticated.

::: rest_framework_services.dispatch.base_pool.base_pool

::: rest_framework_services.dispatch.null_progress.null_progress
