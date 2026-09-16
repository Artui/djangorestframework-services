"""Generic service-layer error.

Framework-agnostic. Mapped to an HTTP response only at the view boundary.
"""

from __future__ import annotations


class ServiceError(Exception):
    """Raised by services to signal a business-rule failure.

    Carries a ``message`` and nothing else, so the view layer can surface it
    without the service depending on DRF. A structured payload belongs to a
    member that declares one:
    [`ServiceValidationError`][rest_framework_services.exceptions.service_validation_error.ServiceValidationError]
    carries a field-keyed ``detail``,
    [`AdditionalInputRequired`][rest_framework_services.exceptions.additional_input_required.AdditionalInputRequired]
    a ``schema``, and
    [`ActionUnavailable`][rest_framework_services.exceptions.action_unavailable.ActionUnavailable]
    a ``code``.
    """

    default_message: str = "Service error."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message if message is not None else self.default_message)
        self.message: str = message if message is not None else self.default_message
