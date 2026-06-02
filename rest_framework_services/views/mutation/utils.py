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
from rest_framework_services.types.selector_spec import SelectorSpec
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
    output_selector_spec: SelectorSpec[Any, Any] | None,
    atomic: bool,
    success_status: int,
    instance: Any,
    extra_kwargs: dict[str, Any] | None = None,
    extra_input_data: Mapping[str, Any] | None = None,
    input_context: dict[str, Any] | None = None,
    resolve_output_context: Callable[[Any], dict[str, Any]] | None = None,
    partial: bool = False,
) -> Response:
    """Internal flow runner shared by ``MutationFlowMixin`` and ``@service_action``.

    Steps:
      1. Validate input → dataclass instance.
      2. Build kwarg pool (request, user, instance?, data?, extras).
      3. Resolve service signature against pool, dispatch.
      4. Map ``ServiceError`` → DRF exception on raise.
      5. If ``output_selector_spec`` carries a selector, invoke it (with the
         service result added to the pool as ``result``) and apply the
         spec's queryset shaping; materialize a QuerySet via ``.first()``
         since the nested spec is always retrieve-shaped. Otherwise fall
         back to the in-memory ``instance`` when the service returned None.
      6. Render via the nested spec's ``output_serializer`` (or raw, or 204).

    ``resolve_output_context`` builds the output serializer's ``context=``
    dict. It is called with the *final* ``result`` (post-selector,
    post-fallback) so the output context provider can see — and run a single
    batched query against — the exact instance being serialized. Resolving
    it lazily here, rather than eagerly in :func:`dispatch_mutation_for_spec`,
    is what makes ``result`` available to the provider.

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

    output_serializer: type[Serializer] | None = None
    if output_selector_spec is not None:
        output_serializer = output_selector_spec.output_serializer
        selector = output_selector_spec.selector
        if selector is not None:
            selector_pool: dict[str, Any] = {**pool, "result": result}
            result = run_selector(
                selector,
                resolve_callable_kwargs(selector, selector_pool),
            )
            result = apply_queryset_shaping(
                result,
                view,
                request,
                select_related=output_selector_spec.select_related,
                prefetch_related=output_selector_spec.prefetch_related,
                annotations=output_selector_spec.annotations,
                extend_queryset=output_selector_spec.extend_queryset,
                source_label="ServiceSpec.output_selector_spec.selector",
            )
            if hasattr(result, "first"):
                # Materialize a QuerySet return to a single instance — the
                # nested spec is retrieve-shaped, so a user can write
                # ``selector=lambda result: Model.objects.filter(pk=result.pk)``
                # and rely on the spec's shaping to apply.
                result = result.first()

    if (
        result is None
        and instance is not None
        and success_status != drf_status.HTTP_204_NO_CONTENT
        and (output_selector_spec is None or output_selector_spec.selector is None)
    ):
        # Service mutated in place and returned nothing — render the in-memory
        # instance, mirroring DRF's ``UpdateAPIView`` shape. Skipped for 204
        # responses (destroy) where the fallback would surface a stale
        # post-delete instance with no useful body, and skipped when an
        # output selector ran (its None return is authoritative).
        result = instance

    if output_serializer is not None:
        output_context = resolve_output_context(result) if resolve_output_context else {}
        serializer = output_serializer(result, context=output_context)
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
    output_spec = spec.output_selector_spec
    output_provider = output_spec.output_serializer_context if output_spec is not None else None

    def resolve_output_context(result: Any) -> dict[str, Any]:
        # Resolved lazily with the final ``result`` so the output context
        # provider can run a single batched query against the exact instance
        # being serialized (offered as the ``result`` extra).
        return resolve_serializer_context(
            view,
            request,
            direction_hook="get_output_serializer_context",
            action_hook=action_output_context_hook,
            spec_provider=output_provider,
            extras={"result": result},
        )

    return _execute_mutation(
        view,
        request,
        service=spec.service,
        input_serializer=spec.input_serializer,
        output_selector_spec=output_spec,
        atomic=spec.atomic,
        success_status=success_status,
        instance=instance,
        extra_kwargs=extras,
        extra_input_data=input_extras,
        input_context=input_context,
        resolve_output_context=resolve_output_context,
        partial=partial,
    )
