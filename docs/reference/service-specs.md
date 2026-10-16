# Service specs on the kernel

A `ServiceSpec` or `SelectorSpec` declared here is a source for
[django-service-specs](https://github.com/Artui/django-service-specs): it answers
`as_kernel_spec()`, so the kernel's `kernel_spec(spec)` compiles it once, and
`dispatch`, `authorize` and a `SpecRegistry` take the drfs spec itself. Every
drfs field lands on a kernel seat or is named below as the HTTP views' own.

A `many` spec's list is judged differently by the two routes.
[`dispatch_spec`](dispatch.md) judges the list on the rows sent: where the list
as sent is within its length bounds, a row that is no object is refused alone,
and the list's `validate()` is left to the rows as sent. The kernel's `dispatch`
called directly on a drfs spec has no such body to hold the rows and judges the
list on the rows that survived, in the kernel's own classes and words:
`[good, 1]` under a minimum of two answers row 1's `Expected an object.` beside
`Ensure this field has at least 2 elements.`, and a list-level `validate()` runs
over the rows left.

## What a spec declares for a caller off HTTP

Over HTTP a URLconf captures a nested route's `project_pk` and a query string
carries `fields`. Off HTTP somebody has to say they exist, so a spec declares
them itself:

```python
from rest_framework_services import QueryParam, SelectorKind, SelectorSpec, UrlKwarg

list_tasks = SelectorSpec(
    kind=SelectorKind.LIST,
    selector=tasks_for_project,
    reads=[
        UrlKwarg("project_pk", type="integer", required=True),
        QueryParam("fields", description="Fields to return."),
    ],
)
```

- **`reads`**: each `UrlKwarg` and `QueryParam` becomes one kernel `Parameter`,
  so it is an argument a caller sends and the schema lists. An entry of the
  same name as a selector parameter replaces the reflected one. A sequence is
  required, and a name may be declared once. A selector taking a bare
  `**kwargs` is not handed a route capture no callable declares: declare it
  here or as a keyword parameter. A `UrlKwarg`'s `type` has to be the one the
  route's converter yields: `UrlKwarg("pk", type="integer")` for `<int:pk>/`, since the
  default `string` refuses an `int` capture as `Expected a string.`.
- **`field_audiences`**: per-field markings layered over the output
  serializer's own. On a `ServiceSpec` they layer over its output selector's.
  A spec declaring them with no output serializer is refused at compile, with
  a message naming both.
- **`agent_contract()`**: the `OfflineContract` these two declare, which a
  registry entry answers for transports that read it. `None` when both are
  empty.

## Where each field lands

| drfs field | Kernel seat |
| --- | --- |
| `permission_classes` | one `DRFPermissionCheck`; `[]` is `Unrestricted()`, and `None` is no permissions, which a registry refuses |
| `input_serializer` | `validator` (a `SerializerValidator`) |
| `output_selector_spec.output_serializer`, `output_serializer` | `presenter` (a `SerializerPresenter`) |
| `input_serializer_context` | the `SerializerValidator`'s `serializer_context` |
| `output_selector_spec.output_serializer_context`, `output_serializer_context` | the `SerializerPresenter`'s `serializer_context` |
| `partial` | `partial`, kept only with an `input_serializer` |
| `many`, `many_argument` | the same; the list bounds come from the serializer's `ListSerializer` and apply off HTTP and on an `OfflineServiceView`, while a live view's bulk body is bounded by the `ListSerializer` alone |
| `extend_queryset` | `extend_queryset`, called as `extend(queryset, view, request)` |
| `affordances` | each service's compiled spec |
| everything else of the same name | carried by name |

A service with no `input_serializer` declares none of its own parameters, so
`kernel_spec(spec).parameters()` lists only its target lookup's keys and its
`reads`, and no body reaches the service's keywords. Its signature is declared
only in the variant compiled for an explicit
`ArgumentBinding.SPREAD_AUTHOR_WINS` (`kernel_spec_variant(spec, spread=True)`).
A selector always takes its arguments from its signature, except for the names
its `filter_set` owns.
Parameters marked `NotClientInput` on any callable of the call are hidden.
So are the names the pool fills: `user`, `request`, `view`, `queryset`,
`result` and the rest.

`parameters()` is not available on every compiled spec. Where a target lookup's
selector reads a name that is also a writable input field, such as renaming a
row by the `slug` that finds it, `kernel_spec(spec).parameters()` and
`as_kernel_spec(spec).parameters()` raise `ImproperlyConfigured` ("Parameters
['slug'] are declared by two sources"), because the kernel declares one source
per name. Such a spec still registers and dispatches, and `spec_to_json_schema`
describes it: the route's capture scopes the lookup, and the body's value is
the field's.

Three `reads` declarations are refused with `ImproperlyConfigured` when the
spec compiles (at registration, or whenever it is dispatched, over HTTP too),
each naming the read:

- a name the pool fills, which would never be the caller's;
- a name the spec's service or selector, or one of its `preconditions`, marks
  `NotClientInput`. Dispatch hands a declared read to each of those callables
  by name, so the same callable would be given the client's value for a
  parameter it keeps the server's. The message names the callable and the
  mark; remove one of the two;
- a name a writable field of the input serializer also takes, declared on the
  spec or on its target lookup. The body's value of that name is the field's,
  so the read would reach neither the service nor the lookup. The message
  names the read, the serializer and, where it reads the name too, the target
  lookup. A `UrlKwarg` the target lookup reads is accepted: that is the
  rename-by-key shape above, where the route's capture scopes the lookup. A
  `many` spec's fields are each row's, and a read-only field and a
  `HiddenField` take nothing from the body, so none is a clash: a
  `UrlKwarg` named like a `HiddenField` whose default reads `view.kwargs` is
  how a route capture scopes a write, and off HTTP the caller's value for it
  is written onto `view.kwargs`, where the default reads it.

  A serializer whose fields cannot be read when the spec compiles - they read
  the request, or query a table `migrate` has not made yet, or a row not yet
  there - is taken to declare no name, so registering it at import never
  needs the database; the call that describes it is where such a read is
  refused. A `many=True` spec whose serializer cannot be built then compiles
  with no list bound of its own, and the list serializer that validates the
  call still holds the list to the bound it declares.

A name only a target lookup marks `NotClientInput` is accepted: the lookup is
never handed a read it hides, so the read is the service's argument and the
lookup keeps its own value.

A nested spec compiles to what the kernel runs:

- its `permission_classes` are not carried;
- an instance lookup's `allow_none` and `output_serializer` are dropped;
- an output selector's `kwargs` are dropped;
- an output selector with no `selector` moves its serializer onto the service
  spec's `presenter`;
- `preconditions` and `progress_reporter`, which would never run there, are
  refused.

`success_status`, `document_service_error` and `response_finalizer` are the
HTTP views' and are not compiled.

The two serializer context callables ride on the adapters and are asked when
the serializer is built for a call, over DRF's own context and the view hooks
`dispatch_spec` registers for the call; see
[Customise serializer context](../recipes/serializer-context.md#through-the-kernel).

## The serializer as Validator and Presenter

`SerializerValidator` is the kernel's `Validator` over an `input_serializer`
(a DRF serializer or a dataclass): its parameters are the serializer's fields,
and it validates a call with the target as `instance`, the call's `partial`,
and a serializer context built for the principal, so `CurrentUserDefault`
reads it. A `HiddenField` or a read-only field is not a parameter, and neither
is a field named like a seed (`user`, `result`, a registered seed's name): the
serializer reads it from the body, so its value lands in `data` and the seed
keeps its own. For a request a live DRF view dispatches, the edge compiles a
lenient variant (`kernel_spec_variant(spec, lenient=True)`) whose parameters
carry their names alone, so DRF's serializer is the only validator and answers
in its own words; off HTTP the parameters carry their types and the kernel
checks them first.
`SerializerPresenter` is the kernel's `Presenter` over an output serializer,
with the spec's `field_audiences` layered over the serializer's own markings.

::: rest_framework_services.service_specs.serializer_validator.SerializerValidator

::: rest_framework_services.service_specs.serializer_presenter.SerializerPresenter

## DRF permission classes off HTTP

`DRFPermissionCheck` asks each class in order against the offline request and
view built for the principal. The first refusal raises
[`NotPermitted`][rest_framework_services.service_specs.not_permitted.NotPermitted]
with that class's `message` and `code`.

Before the class-level check, `view.kwargs` is filled from the call's arguments
with what a route would have captured: every declared `UrlKwarg`, taking its
`default` when the argument is absent or null, and the arguments the spec's
row lookup reads. A declared `QueryParam` goes to `request.query_params`. The
object check asks only about model instances. A create or a collection is
never asked about an object, as on HTTP.

::: rest_framework_services.service_specs.drf_permission_check.DRFPermissionCheck

::: rest_framework_services.service_specs.not_permitted.NotPermitted

::: rest_framework_services.service_specs.build_offline_context.build_offline_context

## The kernel's refusals as DRF's

The kernel raises its own `InvalidArguments`, `NotPermitted` and
`PrincipalUnavailable`, which neither DRF's exception handler nor a transport
catching DRF's `ValidationError` or `PermissionDenied` knows. The edge runs the
kernel inside `translate_dispatch_errors()`, so what `dispatch_spec` and
`adispatch_spec` raise for a kernel refusal is one of the three classes below,
each both the kernel's refusal and DRF's: an `except` written against either
package catches it, `detail` carries `ErrorDetail`s with their codes, and DRF
answers it 400 or 403. The one refusal that is DRF's alone is a `many` spec's
rows sent as a bare list, answered as DRF's list serializer answers them, with
a plain `rest_framework.exceptions.ValidationError`: a body that is no list, a
refused row, a row's undeclared key, or more rows than the bound. So catch
DRF's `ValidationError` for every refused argument.

A caller running the kernel's `dispatch`, `resolve_target` or
`shape_queryset` itself, or calling
[`apply_queryset_shaping`](dispatch.md#apply_queryset_shaping), gets the
kernel's classes and wraps the call in the same block where it wants DRF's.

A deactivated principal is `PrincipalUnavailable`, a 403.

### `translate_dispatch_errors`

::: rest_framework_services.service_specs.translate_dispatch_errors.translate_dispatch_errors

### `InvalidArguments`

::: rest_framework_services.service_specs.invalid_arguments.InvalidArguments

### `PrincipalUnavailable`

::: rest_framework_services.service_specs.principal_unavailable.PrincipalUnavailable

### `register_drf_row_errors`

A nested write's row service written for DRF refuses its row with DRF's
`ValidationError`. Registered, which `dispatch_spec`'s module does when it is
imported, that refusal lands at its row, `{"books": {1: {...}}}`, with its
codes, and is still DRF's `ValidationError`.

::: rest_framework_services.service_specs.register_drf_row_errors.register_drf_row_errors

## Compiling

::: rest_framework_services.service_specs.as_kernel_spec.as_kernel_spec

::: rest_framework_services.service_specs.kernel_spec_variant.kernel_spec_variant
