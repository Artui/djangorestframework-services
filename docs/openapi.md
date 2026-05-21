# OpenAPI / Swagger schemas

`djangorestframework-services` ships an opt-in
[`drf-spectacular`](https://drf-spectacular.readthedocs.io/) integration that
makes the generated OpenAPI document line up with what your `ServiceSpec`
actually does — without scattering `@extend_schema` annotations across
every view.

## Install

```bash
pip install "djangorestframework-services[spectacular]"
# or, with uv:
uv add "djangorestframework-services[spectacular]"
```

## Wire it up

Two settings, one call:

```python
# settings.py
REST_FRAMEWORK = {
    # Required for spectacular to inspect every view, including plain
    # ``GenericViewSet`` classes that use ``@service_action``.
    "DEFAULT_SCHEMA_CLASS": (
        "rest_framework_services.openapi.service_auto_schema.ServiceAutoSchema"
    ),
    # ... your usual REST_FRAMEWORK config
}
```

```python
# myapp/apps.py
from django.apps import AppConfig


class MyAppConfig(AppConfig):
    name = "myapp"

    def ready(self) -> None:
        from rest_framework_services.openapi import enable_openapi
        enable_openapi()
```

`enable_openapi()` attaches `ServiceAutoSchema` to every library view class
(`ServiceCreateView`, `ServiceUpdateView`, `ServiceDeleteView`,
`SelectorListView`, `SelectorRetrieveView`, `ServiceViewSet`,
`SelectorViewSet`). The `DEFAULT_SCHEMA_CLASS` setting covers anything
else.

## What you get for free

For mutation surfaces, the response shape is read from
`spec.output_selector_spec.output_serializer` (the nested
`SelectorSpec`). For read surfaces it's read from
`spec.output_serializer` directly.

| Surface                              | Request body              | Success response                                       | 422 contract |
| ------------------------------------ | ------------------------- | ------------------------------------------------------ | ------------ |
| `ServiceCreateView`                  | `spec.input_serializer` ✓ | `spec.output_selector_spec.output_serializer` ✓        | ✓            |
| `ServiceUpdateView`                  | `spec.input_serializer` ✓ | `spec.output_selector_spec.output_serializer` ✓        | ✓            |
| `ServiceDeleteView`                  | `spec.input_serializer` ✓ | `spec.output_selector_spec.output_serializer` ✓        | ✓            |
| `ServiceViewSet["create"]`           | `spec.input_serializer` ✓ | `spec.output_selector_spec.output_serializer` ✓        | ✓            |
| `ServiceViewSet["update"]`           | `spec.input_serializer` ✓ | `spec.output_selector_spec.output_serializer` ✓        | ✓            |
| `ServiceViewSet["partial_update"]`   | `spec.input_serializer` (partial) ✓ | `spec.output_selector_spec.output_serializer` ✓ | ✓     |
| `ServiceViewSet["destroy"]`          | `spec.input_serializer` ✓ | `spec.output_selector_spec.output_serializer` ✓        | ✓            |
| `@service_action`                    | `spec.input_serializer` ✓ | `spec.output_selector_spec.output_serializer` ✓        | ✓            |
| `SelectorListView` / `["list"]`      | n/a                       | `spec.output_serializer` ✓                             | n/a          |
| `SelectorRetrieveView` / `["retrieve"]` | n/a                    | `spec.output_serializer` ✓                             | n/a          |

### Bare dataclass `input_serializer`

The runtime auto-wraps a bare `@dataclass` type in a `DataclassSerializer`.
The schema generator does the same — your dataclass shows up as a typed
component, not a plain `object`.

### Success status codes

Default to the action's HTTP code (201 / 200 / 204) unless
`spec.success_status` overrides it. The schema picks up whichever value
the runtime would return.

### 422 ServiceError responses

`ServiceAutoSchema` attaches a `422` response with a single `detail`
field to every spec-driven mutation, documenting the contract that
`ServiceError` produces at runtime. The component is exposed as
`rest_framework_services.openapi.ServiceErrorSerializer` if you want to
re-use it elsewhere.

## Override per view

`@extend_schema` annotations always win. Use them for the rare case
where the spec doesn't capture the full contract:

```python
from drf_spectacular.utils import extend_schema, OpenApiResponse

class _MyViewSet(ServiceViewSet):
    action_specs = {"create": ServiceSpec(...)}

    @extend_schema(responses={201: MyCustomSerializer, 409: OpenApiResponse(...)})
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)
```

## What's not covered

- Field-level shapes for 400 `ValidationError` bodies (DRF doesn't have a
  great way to express this, and spectacular's defaults are passable).
- Auto-generated named components for `ChangeResult` and similar internal
  value types — they render inline.
- A dedicated `OpenApiSerializerExtension` for `DataclassSerializer`
  giving named components in `#/components/schemas/...` instead of
  inline anonymous schemas. Deferred; spectacular's default rendering is
  usable.
