# Reference

Autodocumented public API. Every page reads the docstrings and
signatures from the source — when in doubt, follow the source link
("Edit this page" in the top right) and read the leaf module.

- **[Views](views.md)** — `ServiceCreateView`, `ServiceUpdateView`,
  `ServiceDeleteView`, `SelectorListView`, `SelectorRetrieveView`,
  `MutationFlowMixin`, `ServiceView` Protocol, kwarg-resolution helpers,
  spec validation.
- **[Viewsets](viewsets.md)** — `ServiceViewSet`, `SelectorViewSet`,
  per-action mixins, `ActionSerializerResolver`, `@service_action`,
  `@selector_action`.
- **[Mutations](mutations.md)** — `apply_input`, `create_from_input`,
  `update_from_input` and async siblings.
- **[Services](services.md)** — service Protocols, the `implements`
  decorator, and the `call_service` / `acall_service` HTTP-scope helpers.
- **[Selectors](selectors.md)** — selector Protocols and the
  `call_selector` / `acall_selector` HTTP-scope helpers.
- **[Types](types.md)** — `ServiceSpec`, `SelectorSpec`,
  `PolymorphicServiceSpec`, `ChangeResult`, `FieldChange`, `UNSET`,
  `UnsetType`, `NoInput`, `HttpExtras`.
- **[Audience projection](audience.md)** — `build_audience_projection`,
  `project_payload`, `annotate_output_schema`: keep one serializer for your
  API and your agent tools, and declare per field which are content, which
  are opaque handles, and which a model should never read out.
- **[Spec registry](registry.md)** — `SpecRegistry`, `RegisteredSpec`:
  one declaration site for a spec set that several transports read.
- **[Exceptions](exceptions.md)** — `ServiceError`,
  `ServiceValidationError`.
- **[OpenAPI](openapi.md)** — `enable_openapi`, `ServiceAutoSchema`,
  `ServiceErrorSerializer` (opt-in `drf-spectacular` adapter).

## Public surface

The top-level `rest_framework_services` package re-exports the user-
facing API. Everything below is supported, but deeper imports
(`rest_framework_services.viewsets`, `rest_framework_services.views`,
`rest_framework_services.mutations`, `rest_framework_services.types`,
`rest_framework_services.exceptions`, `rest_framework_services.selectors`,
`rest_framework_services.services`) are stable too.

```python
from rest_framework_services import (
    # views
    ServiceCreateView,
    ServiceUpdateView,
    ServiceDeleteView,
    SelectorListView,
    SelectorRetrieveView,
    MutationFlowMixin,
    # viewsets
    ServiceViewSet,
    SelectorViewSet,
    ServiceCreateMixin,
    ServiceUpdateMixin,
    ServiceDestroyMixin,
    SelectorListMixin,
    SelectorRetrieveMixin,
    ActionSerializerResolver,
    service_action,
    selector_action,
    # mutations
    apply_input,
    create_from_input,
    update_from_input,
    acreate_from_input,
    aupdate_from_input,
    # services
    CreateService,
    UpdateService,
    DeleteService,
    create_model,
    update_model,
    delete_model,
    acreate_model,
    aupdate_model,
    adelete_model,
    call_service,
    acall_service,
    implements,
    # selectors
    Selector,
    AsyncSelector,
    ListSelector,
    RetrieveSelector,
    call_selector,
    acall_selector,
    # types
    ServiceSpec,
    SelectorSpec,
    SelectorKind,
    ServiceView,
    ChangeResult,
    FieldChange,
    UNSET,
    UnsetType,
    NoInput,
    HttpExtras,
    # registry
    SpecRegistry,
    RegisteredSpec,
    # exceptions
    ServiceError,
    ServiceValidationError,
)
```
