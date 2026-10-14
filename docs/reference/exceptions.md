# Exceptions

Framework-agnostic exceptions raised by services. The view boundary
maps them to DRF responses — see [Errors & atomic](../errors-and-atomic.md)
for the mapping.

Each class is django-service-specs' own, re-exported at the drfs path shown, so
`rest_framework_services.ServiceError is django_service_specs.ServiceError`. An
`except` written against either package catches what the other raises, and the
documentation below is the kernel's.

`ServiceValidationError` takes a lazy translation (`gettext_lazy("...")`) as its
message, as well as a `str`; any other detail still answers with the default
message.

::: rest_framework_services.exceptions.service_error.ServiceError

::: rest_framework_services.exceptions.service_validation_error.ServiceValidationError

::: rest_framework_services.exceptions.service_not_found.ServiceNotFound

::: rest_framework_services.exceptions.service_conflict.ServiceConflict

::: rest_framework_services.exceptions.action_unavailable.ActionUnavailable

::: rest_framework_services.exceptions.additional_input_required.AdditionalInputRequired
