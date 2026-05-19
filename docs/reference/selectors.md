# Selectors

## Protocols

Each Protocol takes a trailing `ExtraT` TypeVar with a default — supply two
arguments for the lenient form (`ListSelector[Author]`), three for the strict
form (`ListSelector[Author, MyExtras]`). See the
[services reference](services.md) for the rationale.

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
