"""Helpers used by the mutation views, viewset mixins, and ``@service_action``.

Public leaf helpers:

- ``validate_input`` — turn ``request.data`` into the serializer's
  ``validated_data`` (dict for ``ModelSerializer``, dataclass instance for
  dataclass-based serializers).
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

from collections.abc import Callable, Mapping
from dataclasses import is_dataclass
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
from rest_framework_services.selectors.utils import apply_queryset_shaping, run_selector
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.views.utils import (
    resolve_callable_kwargs,
    resolve_extra_kwargs,
    resolve_input_extras,
    resolve_serializer_context,
)


class _ServiceAPIException(drf_exceptions.APIException):
    """Default DRF mapping for non-validation :class:`ServiceError`."""

    status_code = drf_status.HTTP_422_UNPROCESSABLE_ENTITY
    default_detail = "Service error."
    default_code = "service_error"


def validate_input(
    request: Request,
    input_serializer: type | None,
    *,
    partial: bool = False,
    extra_data: Mapping[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> Any:
    """Validate ``request.data`` against ``input_serializer``; ``None`` if absent.

    ``input_serializer`` may be:

    - a bare dataclass type — wrapped in a ``DataclassSerializer`` on the fly;
      ``validated_data`` is a dataclass instance;
    - a ``DataclassSerializer`` subclass — instantiated directly;
      ``validated_data`` is a dataclass instance;
    - any other ``Serializer`` subclass (e.g. ``ModelSerializer``) —
      instantiated directly; ``validated_data`` is a ``dict``.

    ``extra_data`` (when supplied) is merged on top of ``request.data``
    before the serializer instantiates — server-provided keys win on
    overlap. This is the seam used by the ``input_data`` resolver chain
    to lift URL kwargs into serializer input.

    ``context`` (when supplied) is forwarded to the serializer's ``context=``
    kwarg so DRF-style ``self.context["request"]`` / ``["view"]`` lookups
    work inside validators and fields.

    The serializer's ``validated_data`` is returned (never ``.save()``); the
    service is responsible for persistence.
    """
    if input_serializer is None:
        return None
    if extra_data:
        data: Any = {**request.data, **extra_data}
    else:
        data = request.data
    serializer_kwargs: dict[str, Any] = {"data": data, "partial": partial}
    if context is not None:
        serializer_kwargs["context"] = context
    if isinstance(input_serializer, type) and issubclass(input_serializer, Serializer):
        serializer: Serializer = input_serializer(**serializer_kwargs)
    elif is_dataclass(input_serializer):
        serializer = DataclassSerializer(
            dataclass=input_serializer,
            **serializer_kwargs,
        )
    else:
        raise TypeError(
            "input_serializer must be a dataclass type or a Serializer subclass; "
            f"got {input_serializer!r}."
        )
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


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
    input_serializer: type | None,
    output_serializer: type[Serializer] | None,
    output_selector: Callable[..., Any] | None,
    atomic: bool,
    success_status: int,
    instance: Any,
    extra_kwargs: dict[str, Any] | None = None,
    extra_input_data: Mapping[str, Any] | None = None,
    input_context: dict[str, Any] | None = None,
    output_context: dict[str, Any] | None = None,
    select_related: Any = None,
    prefetch_related: Any = None,
    annotations: Any = None,
    extend_queryset: Any = None,
    partial: bool = False,
) -> Response:
    """Internal flow runner shared by ``MutationFlowMixin`` and ``@service_action``.

    Steps:
      1. Validate input → dataclass instance.
      2. Build kwarg pool (request, user, instance?, data?, extras).
      3. Resolve service signature against pool, dispatch.
      4. Map ``ServiceError`` → DRF exception on raise.
      5. Apply ``output_selector`` if set; else fall back to in-memory instance
         when service returned None and instance is available.
      6. Render via ``output_serializer`` (or raw, or 204).

    ``view`` is intentionally absent from the pool: services and selectors are
    plain business logic and should not reach back into the calling view. When
    a callable needs view state (URL kwargs, action name, etc.), pipe it
    through ``ServiceSpec.kwargs`` / ``SelectorSpec.kwargs`` instead.
    """
    data: Any = validate_input(
        request,
        input_serializer,
        partial=partial,
        extra_data=extra_input_data,
        context=input_context,
    )
    pool: dict[str, Any] = {
        "request": request,
        "user": getattr(request, "user", None),
    }
    if instance is not None:
        pool["instance"] = instance
    if input_serializer is not None:
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
        result = apply_queryset_shaping(
            result,
            view,
            request,
            select_related=select_related,
            prefetch_related=prefetch_related,
            annotations=annotations,
            extend_queryset=extend_queryset,
            source_label="ServiceSpec.output_selector",
        )
        if hasattr(result, "first"):
            # Materialize a QuerySet return to a single instance — matches
            # the retrieve dispatcher's behaviour and means a user can write
            # ``output_selector=lambda result: Model.objects.filter(pk=result.pk)``
            # and rely on the spec's shaping to apply.
            result = result.first()
    elif (
        result is None and instance is not None and success_status != drf_status.HTTP_204_NO_CONTENT
    ):
        # Service mutated in place and returned nothing — render the in-memory
        # instance, mirroring DRF's ``UpdateAPIView`` shape. Skipped for 204
        # responses (destroy) where the fallback would surface a stale
        # post-delete instance with no useful body.
        result = instance

    if output_serializer is not None:
        serializer = output_serializer(result, context=output_context or {})
        return Response(serializer.data, status=success_status)
    if result is None:
        return Response(status=drf_status.HTTP_204_NO_CONTENT)
    return Response(result, status=success_status)


def dispatch_mutation_for_spec(
    view: Any,
    request: Request,
    spec: ServiceSpec[Any, Any, Any],
    *,
    instance: Any,
    success_status: int,
    partial: bool = False,
) -> Response:
    """End-to-end dispatch for one ``ServiceSpec`` call.

    Runs the kwargs-resolution chain (``spec.kwargs`` →
    ``get_<action>_service_kwargs`` → ``get_service_kwargs``) and the
    underlying mutation flow. Used by :class:`MutationFlowMixin`,
    standalone mutation views, and ``@service_action`` so the call shape
    lives in one place.
    """
    action: str | None = getattr(view, "action", None)
    action_kwargs_hook: str | None = f"get_{action}_service_kwargs" if action else None
    action_input_hook: str | None = f"get_{action}_input_data" if action else None
    action_input_context_hook: str | None = (
        f"get_{action}_input_serializer_context" if action else None
    )
    action_output_context_hook: str | None = (
        f"get_{action}_output_serializer_context" if action else None
    )
    extras = resolve_extra_kwargs(
        view,
        request,
        spec_kwargs=spec.kwargs,
        action_hook=action_kwargs_hook,
        catch_all_hook="get_service_kwargs",
    )
    input_extras = resolve_input_extras(
        view,
        request,
        spec_input_data=spec.input_data,
        action_hook=action_input_hook,
        catch_all_hook="get_input_data",
    )
    input_context = resolve_serializer_context(
        view,
        request,
        direction_hook="get_input_serializer_context",
        action_hook=action_input_context_hook,
        spec_provider=spec.input_serializer_context,
    )
    output_context = resolve_serializer_context(
        view,
        request,
        direction_hook="get_output_serializer_context",
        action_hook=action_output_context_hook,
        spec_provider=spec.output_serializer_context,
    )
    return _execute_mutation(
        view,
        request,
        service=spec.service,
        input_serializer=spec.input_serializer,
        output_serializer=spec.output_serializer,
        output_selector=spec.output_selector,
        atomic=spec.atomic,
        success_status=success_status,
        instance=instance,
        extra_kwargs=extras,
        extra_input_data=input_extras,
        input_context=input_context,
        output_context=output_context,
        select_related=spec.select_related,
        prefetch_related=spec.prefetch_related,
        annotations=spec.annotations,
        extend_queryset=spec.extend_queryset,
        partial=partial,
    )
