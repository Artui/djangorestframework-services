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

### `dispatch_spec`

::: rest_framework_services.dispatch.dispatch_spec.dispatch_spec

### `adispatch_spec`

::: rest_framework_services.dispatch.adispatch_spec.adispatch_spec

### `render_spec_output`

::: rest_framework_services.dispatch.render_spec_output.render_spec_output

### `arender_spec_output`

::: rest_framework_services.dispatch.arender_spec_output.arender_spec_output

### `base_serializer_context`

The DRF baseline (`request` / `format` / `view`) that every serializer gets for
free over HTTP, synthesized for the off-HTTP path. `dispatch_spec` and
`render_spec_output` apply it themselves — reach for it directly only when a
transport builds a serializer outside them. See
[Customise serializer context](../recipes/serializer-context.md#off-the-http-path).

::: rest_framework_services.dispatch.base_serializer_context.base_serializer_context

### `DispatchResult`

::: rest_framework_services.types.dispatch_result.DispatchResult

## Input policies

Three optional, caller-side policies let a transport map its wire onto a spec
without `dispatch_spec` baking in HTTP's implicit answers. The **spec declares
*what*** (its inputs, filters, output shape, permissions); the **caller declares
*how*** its flat input becomes callable arguments, how strict to be about
undeclared keys, and how to authorize a resolved target. The defaults reproduce
the pre-policy behaviour exactly, so a caller that passes none is unaffected.

### `ArgumentBinding`

How the flat `params` map onto a dispatched callable's keyword arguments —
bundled as one `data` payload or spread as individual kwargs, and how the spread
ranks against the spec author's `kwargs`.

::: rest_framework_services.types.argument_binding.ArgumentBinding

### `UnknownArguments`

How strict `dispatch_spec` is about `params` keys outside the spec's declared
set — drop them, reject them, or pass them through to the callable.

::: rest_framework_services.types.unknown_arguments.UnknownArguments

### `TargetGuard`

The object-permission hook invoked with the resolved mutation target before the
service runs. Its signature matches `enforce_permissions`, so that primitive is
passed directly — by name, not wrapped in a `lambda`.

::: rest_framework_services.types.target_guard.TargetGuard

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
read-shaping params that are *not* spec inputs reach the serializer off-HTTP:
`SelectorSpec.filter_set` (when you don't hand `filter_data` in another way), and
any serializer that branches on `request.query_params` (django-restql field
selection, custom serializers). It does **not** make DRF `filter_backends`
(`SearchFilter` / `OrderingFilter`) run — the offline path never calls
`filter_queryset`; `filter_set` is the drf-services-native equivalent.

### `enforce_permissions`

::: rest_framework_services.dispatch.enforce_permissions.enforce_permissions

## Shared

### `resolve_callable_kwargs`

::: rest_framework_services.views.utils.resolve_callable_kwargs

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

### `apply_queryset_shaping`

::: rest_framework_services.selectors.utils.apply_queryset_shaping
