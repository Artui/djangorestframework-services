# Selectors

## Protocols

Each Protocol is parameterised on input / instance / result types only;
`**extras` is typed `Any`. Strict-typed extras live on the user's function
signature via `**extras: Unpack[YourKw]` — see
[Typing services and selectors](../typing.md) for the full pattern.

::: rest_framework_services.selectors.selector.Selector

::: rest_framework_services.selectors.async_selector.AsyncSelector

::: rest_framework_services.selectors.list_selector.ListSelector

::: rest_framework_services.selectors.retrieve_selector.RetrieveSelector

::: rest_framework_services.selectors.output_selector.OutputSelector

## Helpers

### `call_selector`

::: rest_framework_services.selectors.call_selector.call_selector

### `acall_selector`

::: rest_framework_services.selectors.acall_selector.acall_selector

## Dispatch

::: rest_framework_services.selectors.utils
