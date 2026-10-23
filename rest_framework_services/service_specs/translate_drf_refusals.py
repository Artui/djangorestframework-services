"""``translate_drf_refusals`` - an author's DRF refusals leave as the kernel's, so a kernel route reads them."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import Final

from django_service_specs.services.service_error import ServiceError
from django_service_specs.types.dispatch_error import DispatchError
from rest_framework.exceptions import (
    APIException,
    NotAuthenticated,
    PermissionDenied,
    ValidationError,
)

from rest_framework_services.service_specs.drf_not_authenticated import DRFNotAuthenticated
from rest_framework_services.service_specs.drf_service_validation_error import (
    DRFServiceValidationError,
)
from rest_framework_services.service_specs.not_permitted import NotPermitted

TRANSLATED_FROM: Final = "_translated_from"
"""The attribute a translated refusal carries the exception it was translated from under.

The DRF edge reads it to restore what the author raised. A refusal drfs
raised itself, such as a permission class answering ``False``, carries none,
so it is never mistaken for one translated here.
"""


@contextmanager
def translate_drf_refusals() -> Generator[None]:
    """Re-raise the DRF refusals raised inside the block as the kernel's classes, still DRF's.

    The reverse of
    [`translate_dispatch_errors`][rest_framework_services.service_specs.translate_dispatch_errors.translate_dispatch_errors]:
    an author writes a service, a selector, a precondition or a permission
    class against DRF and refuses with DRF's exceptions, which a transport
    over the kernel's ``dispatch`` reads as nothing it knows. So the compile
    runs each author callable it hands the kernel inside this block, and
    [`DRFPermissionCheck`][rest_framework_services.service_specs.drf_permission_check.DRFPermissionCheck]
    asks each permission class inside it:

    - ``ValidationError`` leaves as
      [`DRFServiceValidationError`][rest_framework_services.service_specs.drf_service_validation_error.DRFServiceValidationError],
      with the same detail and codes;
    - ``PermissionDenied`` as
      [`NotPermitted`][rest_framework_services.service_specs.not_permitted.NotPermitted],
      with its message and code;
    - ``NotAuthenticated`` as
      [`DRFNotAuthenticated`][rest_framework_services.service_specs.drf_not_authenticated.DRFNotAuthenticated],
      which DRF still answers 401;
    - a subclass of any of the three as that class's twin, with the subclass's
      detail and ``status_code``.

    Each is still DRF's class, so an ``except`` written against DRF's still
    catches it, and DRF's handler answers it with the status the author's
    exception had. It is raised ``from`` the author's, which it carries under
    ``TRANSLATED_FROM`` too, so the DRF edge re-raises the exception itself
    and every route that answered DRF's exception answers it as before.

    Left exactly as raised:

    - a refusal that is already the kernel's, drfs' dual-base classes and a
      twin translated by an inner block among them, which a kernel route
      already reads;
    - any other exception, DRF's ``NotFound``, ``AuthenticationFailed`` and
      ``Throttled`` included, which have no kernel class to leave as.

    Use it as a ``with`` block, around ``await`` too, for the reason
    ``translate_dispatch_errors`` gives.
    """
    try:
        yield
    except APIException as exc:
        twin = _kernel_twin(exc)
        if twin is None:
            raise
        raise twin from exc


def _kernel_twin(exc: APIException) -> APIException | None:
    """The kernel's class ``exc`` leaves as, or ``None`` to leave it as raised."""
    twin = _built(exc)
    if twin is None:
        return None
    # The detail as DRF built it, a mapping a denial may carry included
    # (test_a_permission_denied_with_a_structured_detail_keeps_it), and the
    # status a project's subclass declares (test_a_project_subclass_keeps_its_status).
    twin.detail = exc.detail
    twin.status_code = exc.status_code
    setattr(twin, TRANSLATED_FROM, exc)
    return twin


def _built(exc: APIException) -> APIException | None:
    """The twin of ``exc``'s class, built from its message and code or its detail.

    The kernel's classes first, since drfs' dual classes and a twin are DRF's
    too and would be rebuilt as themselves. One arc to coverage, so each is
    named: test_a_kernel_refusal_leaves_exactly_as_raised fails without the
    first (its ``dual-not-permitted`` case) and without the second (its
    ``twin`` case).
    """
    if isinstance(exc, DispatchError | ServiceError):
        return None
    if isinstance(exc, NotAuthenticated):
        return DRFNotAuthenticated(_message(exc), _code(exc))
    if isinstance(exc, PermissionDenied):
        return NotPermitted(_message(exc), _code(exc))
    if isinstance(exc, ValidationError):
        return DRFServiceValidationError(exc.detail)
    return None


def _message(exc: APIException) -> str | None:
    """A denial's message, or ``None`` for the kernel's default where its detail is no message."""
    return exc.detail if isinstance(exc.detail, str) else None


def _code(exc: APIException) -> str | None:
    return getattr(exc.detail, "code", None)


__all__ = ["TRANSLATED_FROM", "translate_drf_refusals"]
