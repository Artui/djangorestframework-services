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
chains); a transport-neutral spec dispatcher is future work that will
compose the leaves below.

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
