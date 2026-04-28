# Reference

Autodocumented public API. Every page reads the docstrings and
signatures from the source — when in doubt, follow the source link
("Edit this page" in the top right) and read the leaf module.

- **[Views](views.md)** — `ServiceCreateView`, `ServiceUpdateView`,
  `ServiceDeleteView`, `SelectorListView`, `SelectorRetrieveView`,
  `MutationFlowMixin`, kwarg-resolution helpers.
- **[Viewsets](viewsets.md)** — `ServiceViewSet`, `SelectorViewSet`,
  per-action mixins, `MultiSerializerMixin`, `@service_action`.
- **[Mutations](mutations.md)** — `apply_input`, `create_from_input`,
  `update_from_input` and async siblings.
- **[Selectors](selectors.md)** — `Selector` and `AsyncSelector`
  Protocols.
- **[Types](types.md)** — `ServiceSpec`, `ChangeResult`,
  `FieldChange`, `UNSET`.
- **[Exceptions](exceptions.md)** — `ServiceError`,
  `ServiceValidationError`.

## Public surface

The top-level `rest_framework_services` package re-exports the user-
facing API. Everything below is supported, but deeper imports
(`rest_framework_services.viewsets`, `rest_framework_services.views`,
`rest_framework_services.mutations`, `rest_framework_services.types`,
`rest_framework_services.exceptions`, `rest_framework_services.selectors`)
are stable too.

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
    MultiSerializerMixin,
    service_action,
    # mutations
    apply_input,
    create_from_input,
    update_from_input,
    acreate_from_input,
    aupdate_from_input,
    # selectors
    Selector,
    AsyncSelector,
    # types
    ServiceSpec,
    ChangeResult,
    FieldChange,
    UNSET,
    # exceptions
    ServiceError,
    ServiceValidationError,
)
```
