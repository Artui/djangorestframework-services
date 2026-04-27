"""Helpers used by the mutation views, viewset mixins, and ``@service_action``.

Public leaf helpers:

- ``validate_input`` — turn ``request.data`` into a dataclass instance.
- ``dispatch_service`` — sync/async dispatch with optional atomic wrapping.
- ``map_service_error`` — translate a framework-agnostic ``ServiceError``
  into the appropriate DRF exception.
- ``_ServiceAPIException`` — the 422 mapping target.

Internal:

- ``_execute_mutation`` — the underlying flow runner. Used by
  :class:`~rest_framework_services.views.mutation.mutation_flow_mixin.MutationFlowMixin`
  (composed into views / per-action mixins) and by ``@service_action``
  (which can't inherit from a mixin because it's a decorator).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from asgiref.sync import async_to_sync
from rest_framework import exceptions as drf_exceptions
from rest_framework import status as drf_status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.serializers import Serializer
from rest_framework_dataclasses.serializers import DataclassSerializer

from rest_framework_services._compat.arun_service import arun_service
from rest_framework_services._compat.is_async import is_async
from rest_framework_services._compat.run_service import run_service
from rest_framework_services.exceptions.service_error import ServiceError
from rest_framework_services.exceptions.service_validation_error import (
    ServiceValidationError,
)
from rest_framework_services.selectors.utils import run_selector
from rest_framework_services.views.utils import resolve_callable_kwargs


class _ServiceAPIException(drf_exceptions.APIException):
    """Default DRF mapping for non-validation :class:`ServiceError`."""

    status_code = drf_status.HTTP_422_UNPROCESSABLE_ENTITY
    default_detail = "Service error."
    default_code = "service_error"


def validate_input(
    request: Request,
    input_dataclass: type | None,
    *,
    partial: bool = False,
) -> Any:
    """Validate ``request.data`` against ``input_dataclass``; ``None`` if absent."""
    if input_dataclass is None:
        return None
    serializer = DataclassSerializer(
        dataclass=input_dataclass,
        data=request.data,
        partial=partial,
    )
    serializer.is_valid(raise_exception=True)
    return serializer.save()


def dispatch_service(
    fn: Callable[..., Any],
    kwargs: dict[str, Any],
    *,
    atomic: bool,
) -> Any:
    """Run a service from a sync view, transparently bridging async ones."""
    if is_async(fn):
        return async_to_sync(arun_service)(fn, kwargs, atomic=atomic)
    return run_service(fn, kwargs, atomic=atomic)


def map_service_error(exc: ServiceError) -> drf_exceptions.APIException:
    """Translate a framework-agnostic service error into a DRF exception."""
    if isinstance(exc, ServiceValidationError):
        return drf_exceptions.ValidationError(exc.detail)
    return _ServiceAPIException(str(exc))


def _execute_mutation(
    view: Any,
    request: Request,
    *,
    service: Callable[..., Any],
    input_dataclass: type | None,
    output_serializer: type[Serializer] | None,
    output_selector: Callable[..., Any] | None,
    atomic: bool,
    success_status: int,
    instance: Any,
    extra_kwargs: dict[str, Any] | None = None,
    partial: bool = False,
) -> Response:
    """Internal flow runner shared by ``MutationFlowMixin`` and ``@service_action``.

    Steps:
      1. Validate input → dataclass instance.
      2. Build kwarg pool (request, user, view, instance?, data?, extras).
      3. Resolve service signature against pool, dispatch.
      4. Map ``ServiceError`` → DRF exception on raise.
      5. Apply ``output_selector`` if set; else fall back to in-memory instance
         when service returned None and instance is available.
      6. Render via ``output_serializer`` (or raw, or 204).
    """
    data: Any = validate_input(request, input_dataclass, partial=partial)
    pool: dict[str, Any] = {
        "request": request,
        "user": getattr(request, "user", None),
        "view": view,
    }
    if instance is not None:
        pool["instance"] = instance
    if input_dataclass is not None:
        pool["data"] = data
    if extra_kwargs:
        pool.update(extra_kwargs)

    try:
        result: Any = dispatch_service(
            service,
            resolve_callable_kwargs(service, pool),
            atomic=atomic,
        )
    except ServiceError as exc:
        raise map_service_error(exc) from exc

    if output_selector is not None:
        selector_pool: dict[str, Any] = {**pool, "result": result}
        result = run_selector(
            output_selector,
            resolve_callable_kwargs(output_selector, selector_pool),
        )
    elif (
        result is None and instance is not None and success_status != drf_status.HTTP_204_NO_CONTENT
    ):
        # Service mutated in place and returned nothing — render the in-memory
        # instance, mirroring DRF's ``UpdateAPIView`` shape. Skipped for 204
        # responses (destroy) where the fallback would surface a stale
        # post-delete instance with no useful body.
        result = instance

    if output_serializer is not None:
        serializer = output_serializer(result, context={"request": request})
        return Response(serializer.data, status=success_status)
    if result is None:
        return Response(status=drf_status.HTTP_204_NO_CONTENT)
    return Response(result, status=success_status)
