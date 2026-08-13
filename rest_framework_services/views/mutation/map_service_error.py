"""``map_service_error`` — translate a framework-agnostic ``ServiceError`` to DRF.

Kept in its own leaf module (rather than inside ``views.mutation.utils``) so it
can be imported without pulling in the heavy mutation-flow machinery. That
matters for [`call_service`][rest_framework_services.services.call_service.call_service],
which is part of the package's eagerly-imported public API: importing the whole
``utils`` module at package-import time (during ``apps.populate()``) triggers a
circular import, whereas this leaf depends only on DRF exceptions and the
framework-agnostic error types.
"""

from __future__ import annotations

from rest_framework import exceptions as drf_exceptions
from rest_framework import status as drf_status

from rest_framework_services.exceptions.service_conflict import ServiceConflict
from rest_framework_services.exceptions.service_error import ServiceError
from rest_framework_services.exceptions.service_not_found import ServiceNotFound
from rest_framework_services.exceptions.service_validation_error import (
    ServiceValidationError,
)


class _ServiceAPIException(drf_exceptions.APIException):
    """Default DRF mapping for non-validation
    [`ServiceError`][rest_framework_services.exceptions.service_error.ServiceError]."""

    status_code = drf_status.HTTP_422_UNPROCESSABLE_ENTITY
    default_detail = "Service error."
    default_code = "service_error"


class _ConflictAPIException(drf_exceptions.APIException):
    """DRF mapping for
    [`ServiceConflict`][rest_framework_services.exceptions.service_conflict.ServiceConflict].

    Declared here rather than reached for from DRF, which ships no ``409``
    exception of its own."""

    status_code = drf_status.HTTP_409_CONFLICT
    default_detail = "Conflict."
    default_code = "conflict"


def map_service_error(exc: ServiceError) -> drf_exceptions.APIException:
    """Translate a framework-agnostic service error into a DRF exception.

    Specific members first, the generic ``422`` last. Every one of these is a
    ``ServiceError`` subclass, so the order is the mapping: a generic branch reached
    first would swallow all of them, which is the same trap a transport's own
    handler has (see each member's docstring).

    ``AdditionalInputRequired`` deliberately has no branch — "I need one more value"
    is the resource being unprocessable as asked, so it stays a ``422`` and carries
    its own schema in the body.
    """
    if isinstance(exc, ServiceValidationError):
        return drf_exceptions.ValidationError(exc.detail)
    if isinstance(exc, ServiceNotFound):
        return drf_exceptions.NotFound(str(exc))
    if isinstance(exc, ServiceConflict):
        return _ConflictAPIException(str(exc))
    return _ServiceAPIException(str(exc))
