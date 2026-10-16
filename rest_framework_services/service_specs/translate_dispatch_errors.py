"""``translate_dispatch_errors`` - the kernel's refusals leave as the classes DRF's ladders catch."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from django_service_specs.authorization.not_permitted import NotPermitted as KernelNotPermitted
from django_service_specs.authorization.principal_unavailable import (
    PrincipalUnavailable as KernelPrincipalUnavailable,
)
from django_service_specs.parameters.invalid_arguments import (
    InvalidArguments as KernelInvalidArguments,
)
from django_service_specs.types.dispatch_error import DispatchError
from rest_framework.exceptions import APIException

from rest_framework_services.service_specs.invalid_arguments import InvalidArguments
from rest_framework_services.service_specs.not_permitted import NotPermitted
from rest_framework_services.service_specs.principal_unavailable import PrincipalUnavailable


@contextmanager
def translate_dispatch_errors() -> Generator[None]:
    """Re-raise the kernel's ``DispatchError``s inside the block as this package's dual-base classes.

    Every published consumer of this package catches DRF's
    ``ValidationError`` and ``PermissionDenied`` by name, and DRF's exception
    handler answers only those; the kernel's own refusals reach none of them,
    and a view serves one as a 500. So the DRF edge runs the kernel's dispatch
    inside this block:

    - the kernel's ``InvalidArguments`` leaves as
      [`InvalidArguments`][rest_framework_services.service_specs.invalid_arguments.InvalidArguments],
      with the same tree and every message's code;
    - its ``NotPermitted`` as
      [`NotPermitted`][rest_framework_services.service_specs.not_permitted.NotPermitted],
      and its ``PrincipalUnavailable`` as
      [`PrincipalUnavailable`][rest_framework_services.service_specs.principal_unavailable.PrincipalUnavailable],
      each with the same message;
    - a subclass of any of the three leaves as that class's twin, with the
      subclass's message.

    The new exception is raised ``from`` the kernel's, so the traceback keeps
    where dispatch refused. Each dual-base class is still the kernel's, so an
    ``except DispatchError`` around the block catches what leaves it as well.

    Left exactly as raised:

    - anything that is not a ``DispatchError``, a service's own refusals
      among them;
    - a ``DispatchError`` that is already a DRF exception, such as a dual-base
      class a validator raised itself, which both ladders already catch;
    - a ``DispatchError`` with no twin here, which keeps the kernel's class
      rather than be given a DRF answer nobody declared.

    Use it as a ``with`` block, around ``await`` too. Not as a decorator on an
    ``async def``: that would wrap the creation of the coroutine, not its run.
    """
    try:
        yield
    except DispatchError as exc:
        twin = _drf_twin(exc)
        if twin is None:
            raise
        raise twin from exc


def _drf_twin(exc: DispatchError) -> DispatchError | None:
    """The dual-base class ``exc`` leaves as, or ``None`` to leave it as raised.

    One ``isinstance`` per branch, so each has the test that takes it. A dual
    class is a subclass of the kernel's it twins, so the check for a DRF
    exception comes first or a dual ``InvalidArguments`` would be rebuilt as
    itself.
    """
    if isinstance(exc, APIException):
        return None
    if isinstance(exc, KernelInvalidArguments):
        # The kernel's class takes no message, so one a subclass declares is
        # carried across by hand (test_an_invalid_arguments_subclass_keeps_its_message).
        twin = InvalidArguments(exc.detail)
        twin.message = exc.message
        return twin
    if isinstance(exc, KernelNotPermitted):
        return NotPermitted(exc.message)
    if isinstance(exc, KernelPrincipalUnavailable):
        return PrincipalUnavailable(exc.message)
    return None
