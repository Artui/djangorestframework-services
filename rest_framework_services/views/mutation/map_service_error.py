"""``map_service_error`` — translate a framework-agnostic ``ServiceError`` to DRF.

Kept in its own leaf module (rather than inside ``views.mutation.utils``) so it
can be imported without pulling in the heavy mutation-flow machinery. That
matters for :func:`~rest_framework_services.services.call_service.call_service`,
which is part of the package's eagerly-imported public API: importing the whole
``utils`` module at package-import time (during ``apps.populate()``) triggers a
circular import, whereas this leaf depends only on DRF exceptions and the
framework-agnostic error types.
"""

from __future__ import annotations

from rest_framework import exceptions as drf_exceptions
from rest_framework import status as drf_status

from rest_framework_services.exceptions.service_error import ServiceError
from rest_framework_services.exceptions.service_validation_error import (
    ServiceValidationError,
)


class _ServiceAPIException(drf_exceptions.APIException):
    """Default DRF mapping for non-validation :class:`ServiceError`."""

    status_code = drf_status.HTTP_422_UNPROCESSABLE_ENTITY
    default_detail = "Service error."
    default_code = "service_error"


def map_service_error(exc: ServiceError) -> drf_exceptions.APIException:
    """Translate a framework-agnostic service error into a DRF exception."""
    if isinstance(exc, ServiceValidationError):
        return drf_exceptions.ValidationError(exc.detail)
    return _ServiceAPIException(str(exc))
