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

from collections.abc import Mapping
from typing import Any

from rest_framework import exceptions as drf_exceptions
from rest_framework import status as drf_status

from rest_framework_services.exceptions.action_unavailable import ActionUnavailable
from rest_framework_services.exceptions.additional_input_required import (
    AdditionalInputRequired,
)
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


class _AdditionalInputAPIException(_ServiceAPIException):
    """The ``422`` an
    [`AdditionalInputRequired`][rest_framework_services.exceptions.additional_input_required.AdditionalInputRequired]
    carrying a schema answers with: ``{"detail": <message>, "schema": <schema>}``.

    The schema is set on ``detail`` after construction rather than passed in,
    because DRF coerces every leaf of a detail it is given to ``ErrorDetail``,
    which is a ``str``. Passed in, ``"default": False`` reached the client as
    ``"False"`` and ``"maximum": 3`` as ``"3"``, so the body was no longer JSON
    Schema and a client rendering a form from it read a boolean default as a
    non-empty string. The message is still coerced, so ``detail`` keeps the
    ``ErrorDetail`` and code every other ``422`` has.

    ``get_codes`` and ``get_full_details`` are answered here for the same
    reason: DRF's own walk reads ``.code`` off every leaf, and a schema value has
    none. Both describe the message as DRF does and carry the schema as it was
    raised, since it is data rather than an error with a code.
    """

    def __init__(self, message: str, schema: Mapping[str, Any]) -> None:
        super().__init__()
        self.message = drf_exceptions.ErrorDetail(message, code=self.default_code)
        self.schema: dict[str, Any] = dict(schema)
        self.detail = {"detail": self.message, "schema": self.schema}

    def get_codes(self) -> dict[str, Any]:
        return {"detail": self.message.code, "schema": self.schema}

    def get_full_details(self) -> dict[str, Any]:
        return {
            "detail": {"message": self.message, "code": self.message.code},
            "schema": self.schema,
        }


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

    ``ActionUnavailable`` is a ``ServiceConflict`` and keeps its ``409``; its
    branch exists only to put the affordance's ``code`` in the body beside the
    ``detail`` a client already reads.

    ``AdditionalInputRequired`` takes no *status* branch — "I need one more value"
    is the resource being unprocessable as asked, so it stays a ``422`` like any
    other service error. It does take a *body* branch, because the schema naming
    what is missing is the whole point of the error: without it a client is told
    that something is needed and not what, and the answer comes back as ordinary
    input on every transport.
    """
    if isinstance(exc, ServiceValidationError):
        return drf_exceptions.ValidationError(exc.detail)
    if isinstance(exc, ServiceNotFound):
        return drf_exceptions.NotFound(str(exc))
    if isinstance(exc, ActionUnavailable):
        # A body branch rather than a status one, for the reason
        # ``AdditionalInputRequired`` takes one below: the stable code is the
        # whole point of the member, and a client left with only the sentence has
        # nothing to branch on. Always present, since the member cannot be built
        # without one.
        return _ConflictAPIException({"detail": str(exc), "code": exc.code})
    if isinstance(exc, ServiceConflict):
        return _ConflictAPIException(str(exc))
    if isinstance(exc, AdditionalInputRequired) and exc.schema is not None:
        # A mapping detail renders as the object itself, so ``detail`` keeps the
        # shape a client already reads and ``schema`` joins it rather than
        # replacing it, served as it was raised. Only when there is a schema: the
        # error is valid without one, and growing the body unconditionally would
        # change every plain message into an object for no gain.
        return _AdditionalInputAPIException(str(exc), exc.schema)
    return _ServiceAPIException(str(exc))
