"""``adispatch_spec`` — async sibling of :func:`dispatch_spec`."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from asgiref.sync import sync_to_async
from django.core.exceptions import ImproperlyConfigured, ObjectDoesNotExist

from rest_framework_services.dispatch.utils import (
    COLLECTION_SOURCE,
    INSTANCE_SOURCE,
    OUTPUT_SOURCE,
    SELECTOR_SOURCE,
    arun_callable,
    arun_service_callable,
    base_pool,
    resolve_input_context,
    resolve_provider,
    shape_queryset,
)
from rest_framework_services.selectors.utils import is_queryset
from rest_framework_services.types.dispatch_result import DispatchResult
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.views.mutation.utils import build_input_serializer_from_data
from rest_framework_services.views.utils import resolve_callable_kwargs


async def adispatch_spec(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any],
    *,
    user: Any,
    params: Mapping[str, Any] | list[Any],
    request: Any = None,
    view: Any = None,
    success_status: int | None = None,
) -> DispatchResult:
    """Async :func:`~rest_framework_services.dispatch_spec`.

    Same contract and :class:`DispatchResult` shape; async selectors / services
    are awaited and sync ones run in Django's thread-sensitive executor so the
    ORM stays safe off the event loop. A ``LIST`` result is returned as the
    (lazy) shaped queryset — the async transport materializes / paginates it in
    a thread, exactly as on the sync path.
    """
    if isinstance(spec, ServiceSpec):
        return await _adispatch_service(
            spec,
            user=user,
            params=params,
            request=request,
            view=view,
            success_status=success_status,
        )
    if isinstance(spec, SelectorSpec):
        return await _adispatch_selector(spec, user=user, params=params, request=request, view=view)
    raise TypeError(
        f"adispatch_spec expects a ServiceSpec or SelectorSpec; got {type(spec).__name__}."
    )


async def _adispatch_selector(
    spec: SelectorSpec[Any, Any],
    *,
    user: Any,
    params: Any,
    request: Any,
    view: Any,
) -> DispatchResult:
    if spec.selector is None:
        raise ImproperlyConfigured("adispatch_spec requires the SelectorSpec to set a `selector`.")
    pool: dict[str, Any] = {**base_pool(user=user, request=request), **params}
    pool.update(resolve_provider(spec.kwargs, {"view": view, "request": request}))
    try:
        result: Any = await arun_callable(
            spec.selector, resolve_callable_kwargs(spec.selector, pool)
        )
        result = shape_queryset(
            spec, result, view=view, request=request, params=params, source_label=SELECTOR_SOURCE
        )
    except ObjectDoesNotExist:
        if spec.kind is SelectorKind.RETRIEVE:
            return _missing_or_null(spec)
        raise

    if spec.kind is not SelectorKind.RETRIEVE:
        return DispatchResult(value=result, kind="list", status=200)
    instance: Any = await result.afirst() if is_queryset(result) else result
    if instance is None:
        return _missing_or_null(spec)
    return DispatchResult(value=instance, kind="instance", status=200)


def _missing_or_null(spec: SelectorSpec[Any, Any]) -> DispatchResult:
    if spec.allow_none:
        return DispatchResult(value=None, kind="instance", status=200)
    return DispatchResult(value=None, kind="not_found", status=404)


async def _adispatch_service(
    spec: ServiceSpec[Any, Any, Any],
    *,
    user: Any,
    params: Any,
    request: Any,
    view: Any,
    success_status: int | None,
) -> DispatchResult:
    if spec.many:
        return await _adispatch_service_many(
            spec,
            user=user,
            params=params,
            request=request,
            view=view,
            success_status=success_status,
        )

    mode, target = await _aresolve_target(
        spec, user=user, params=params, request=request, view=view
    )
    if mode == "missing":
        return DispatchResult(value=None, kind="not_found", status=404)
    instance = target if mode == "instance" else None

    input_context = resolve_input_context(spec, view=view, request=request)
    # Validation can touch the DB (e.g. ``UniqueValidator``); run it off-loop.
    serializer = await sync_to_async(build_input_serializer_from_data, thread_sensitive=True)(
        dict(params),
        spec.input_serializer,
        partial=spec.partial or False,
        context=input_context,
        instance=instance,
    )

    pool: dict[str, Any] = base_pool(user=user, request=request)
    pool.update(resolve_provider(spec.kwargs, {"view": view, "request": request}))
    if mode == "collection":
        pool["collection"] = target
    elif instance is not None:
        pool["instance"] = instance
    if serializer is not None:
        pool["data"] = serializer.validated_data
        pool["serializer"] = serializer

    result: Any = await arun_service_callable(
        spec.service, resolve_callable_kwargs(spec.service, pool), atomic=spec.atomic
    )
    result = await _arun_output_selector(
        spec, result, user=user, request=request, view=view, params=params
    )

    status = success_status if success_status is not None else (spec.success_status or 200)
    return DispatchResult(value=result, kind="instance", status=status)


async def _adispatch_service_many(
    spec: ServiceSpec[Any, Any, Any],
    *,
    user: Any,
    params: Any,
    request: Any,
    view: Any,
    success_status: int | None,
) -> DispatchResult:
    input_context = resolve_input_context(spec, view=view, request=request)
    serializer = await sync_to_async(build_input_serializer_from_data, thread_sensitive=True)(
        params,
        spec.input_serializer,
        partial=spec.partial or False,
        many=True,
        context=input_context,
    )
    pool: dict[str, Any] = base_pool(user=user, request=request)
    pool.update(resolve_provider(spec.kwargs, {"view": view, "request": request}))
    if serializer is not None:
        pool["data"] = serializer.validated_data
        pool["serializer"] = serializer
    result: Any = await arun_service_callable(
        spec.service, resolve_callable_kwargs(spec.service, pool), atomic=spec.atomic
    )
    status = success_status if success_status is not None else (spec.success_status or 200)
    return DispatchResult(value=result, kind="list", status=status)


async def _aresolve_target(
    spec: ServiceSpec[Any, Any, Any],
    *,
    user: Any,
    params: Mapping[str, Any],
    request: Any,
    view: Any,
) -> tuple[str, Any]:
    coll_spec = spec.collection_selector_spec
    if coll_spec is not None:
        if coll_spec.selector is None:
            raise ImproperlyConfigured(
                "collection_selector_spec requires a `selector` resolving the target set."
            )
        pool: dict[str, Any] = {**base_pool(user=user, request=request), **params}
        pool.update(resolve_provider(coll_spec.kwargs, {"view": view, "request": request}))
        result: Any = await arun_callable(
            coll_spec.selector, resolve_callable_kwargs(coll_spec.selector, pool)
        )
        collection = shape_queryset(
            coll_spec,
            result,
            view=view,
            request=request,
            params=params,
            source_label=COLLECTION_SOURCE,
        )
        return ("collection", collection)
    found, instance = await _aresolve_instance(
        spec, user=user, params=params, request=request, view=view
    )
    return ("instance", instance) if found else ("missing", None)


async def _arun_output_selector(
    spec: ServiceSpec[Any, Any, Any],
    result: Any,
    *,
    user: Any,
    request: Any,
    view: Any,
    params: Mapping[str, Any],
) -> Any:
    out_spec = spec.output_selector_spec
    if out_spec is None or out_spec.selector is None:
        return result
    pool: dict[str, Any] = {
        **base_pool(user=user, request=request),
        "instance": result,
        "result": result,
    }
    selected: Any = await arun_callable(
        out_spec.selector, resolve_callable_kwargs(out_spec.selector, pool)
    )
    selected = shape_queryset(
        out_spec, selected, view=view, request=request, params=params, source_label=OUTPUT_SOURCE
    )
    return await selected.afirst() if is_queryset(selected) else selected


async def _aresolve_instance(
    spec: ServiceSpec[Any, Any, Any],
    *,
    user: Any,
    params: Mapping[str, Any],
    request: Any,
    view: Any,
) -> tuple[bool, Any]:
    instance_spec = spec.instance_selector_spec
    if instance_spec is None or instance_spec.selector is None:
        return (True, None)
    pool: dict[str, Any] = {**base_pool(user=user, request=request), **params}
    pool.update(resolve_provider(instance_spec.kwargs, {"view": view, "request": request}))
    try:
        result: Any = await arun_callable(
            instance_spec.selector, resolve_callable_kwargs(instance_spec.selector, pool)
        )
        result = shape_queryset(
            instance_spec,
            result,
            view=view,
            request=request,
            params=params,
            source_label=INSTANCE_SOURCE,
        )
    except ObjectDoesNotExist:
        return (False, None)
    instance: Any = await result.afirst() if is_queryset(result) else result
    if instance is None:
        return (False, None)
    return (True, instance)


__all__ = ["adispatch_spec"]
