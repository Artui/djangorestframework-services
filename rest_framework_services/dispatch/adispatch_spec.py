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
    call_target_guard,
    guard_many_argument_binding,
    merge_arguments,
    resolve_argument_binding,
    resolve_input_context,
    resolve_provider,
    resolve_service_many_input,
    resolve_unknown_arguments,
    service_input,
    shape_queryset,
    view_url_kwargs,
)
from rest_framework_services.selectors.utils import is_queryset
from rest_framework_services.types.argument_binding import ArgumentBinding
from rest_framework_services.types.dispatch_result import DispatchResult
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.types.target_guard import TargetGuard
from rest_framework_services.types.unknown_arguments import UnknownArguments
from rest_framework_services.views.mutation.resolve_success_status import resolve_success_status
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
    argument_binding: ArgumentBinding = ArgumentBinding.AUTO,
    unknown_arguments: UnknownArguments = UnknownArguments.IGNORE,
    on_target_resolved: TargetGuard | None = None,
) -> DispatchResult:
    """Async :func:`~rest_framework_services.dispatch_spec`.

    Same contract, :class:`DispatchResult` shape, and ``argument_binding`` /
    ``unknown_arguments`` / ``on_target_resolved`` policies; async selectors /
    services are awaited and sync ones run in Django's thread-sensitive executor
    so the ORM stays safe off the event loop. A ``LIST`` result is returned as
    the (lazy) shaped queryset — the async transport materializes / paginates it
    in a thread, exactly as on the sync path.

    As on :func:`~rest_framework_services.dispatch_spec`, a ``many=True`` bulk
    spec honours ``unknown_arguments`` per list element and rejects a non-default
    ``argument_binding`` (there is no per-item kwarg to spread into a single
    list-payload service call).
    """
    if isinstance(spec, ServiceSpec):
        return await _adispatch_service(
            spec,
            user=user,
            params=params,
            request=request,
            view=view,
            success_status=success_status,
            argument_binding=argument_binding,
            unknown_arguments=unknown_arguments,
            on_target_resolved=on_target_resolved,
        )
    if isinstance(spec, SelectorSpec):
        return await _adispatch_selector(
            spec,
            user=user,
            params=params,
            request=request,
            view=view,
            argument_binding=argument_binding,
            unknown_arguments=unknown_arguments,
            on_target_resolved=on_target_resolved,
        )
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
    argument_binding: ArgumentBinding,
    unknown_arguments: UnknownArguments,
    on_target_resolved: TargetGuard | None,
) -> DispatchResult:
    if spec.selector is None:
        raise ImproperlyConfigured("adispatch_spec requires the SelectorSpec to set a `selector`.")
    resolve_unknown_arguments(spec, params, unknown_arguments=unknown_arguments, serializer=None)
    binding = resolve_argument_binding(spec, argument_binding)
    pool: dict[str, Any] = base_pool(user=user, request=request)
    merge_arguments(
        pool,
        binding=binding,
        spread_source=params,
        provider_kwargs=resolve_provider(spec.kwargs, {"view": view, "request": request}),
        url_kwargs=view_url_kwargs(view),
    )
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

    # The guard may run ``has_object_permission`` (DB), so keep it off the loop.
    if spec.kind is not SelectorKind.RETRIEVE:
        # LIST: guard the resolved set (per-set / class-level only).
        await sync_to_async(call_target_guard, thread_sensitive=True)(
            on_target_resolved, spec, result, user=user, request=request, view=view
        )
        return DispatchResult(value=result, kind="list", status=200)
    instance: Any = await result.afirst() if is_queryset(result) else result
    if instance is None:
        return _missing_or_null(spec)
    # RETRIEVE: guard the resolved row (object-level permissions run here).
    await sync_to_async(call_target_guard, thread_sensitive=True)(
        on_target_resolved, spec, instance, user=user, request=request, view=view
    )
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
    argument_binding: ArgumentBinding,
    unknown_arguments: UnknownArguments,
    on_target_resolved: TargetGuard | None,
) -> DispatchResult:
    if spec.many:
        return await _adispatch_service_many(
            spec,
            user=user,
            params=params,
            request=request,
            view=view,
            success_status=success_status,
            argument_binding=argument_binding,
            unknown_arguments=unknown_arguments,
            on_target_resolved=on_target_resolved,
        )

    mode, target = await _aresolve_target(
        spec, user=user, params=params, request=request, view=view
    )
    if mode == "missing":
        return DispatchResult(value=None, kind="not_found", status=404)
    # The guard may run ``has_object_permission`` (DB), so keep it off the loop.
    await sync_to_async(call_target_guard, thread_sensitive=True)(
        on_target_resolved, spec, target, user=user, request=request, view=view
    )
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
    extras = resolve_unknown_arguments(
        spec, params, unknown_arguments=unknown_arguments, serializer=serializer
    )
    data, spread_source = service_input(serializer, extras)

    binding = resolve_argument_binding(spec, argument_binding)
    pool: dict[str, Any] = base_pool(user=user, request=request)
    merge_arguments(
        pool,
        binding=binding,
        spread_source=spread_source,
        provider_kwargs=resolve_provider(spec.kwargs, {"view": view, "request": request}),
    )
    if mode == "collection":
        pool["collection"] = target
    elif instance is not None:
        pool["instance"] = instance
    if serializer is not None:
        pool["data"] = data
        pool["serializer"] = serializer
    elif extras:
        pool["data"] = data

    result: Any = await arun_service_callable(
        spec.service, resolve_callable_kwargs(spec.service, pool), atomic=spec.atomic
    )
    output_result, output_is_list = await _arun_output_selector(
        spec, result, user=user, request=request, view=view, params=params
    )

    # A callable ``spec.success_status`` keys on the *service's* return value
    # (``result``), captured before the output selector re-fetch replaced it.
    status_pool: dict[str, Any] = {"request": request, "view": view, "result": result}
    if instance is not None:
        status_pool["instance"] = instance
    status = (
        success_status
        if success_status is not None
        else resolve_success_status(spec.success_status, default=200, pool=status_pool)
    )
    return DispatchResult(
        value=output_result, kind="list" if output_is_list else "instance", status=status
    )


async def _adispatch_service_many(
    spec: ServiceSpec[Any, Any, Any],
    *,
    user: Any,
    params: Any,
    request: Any,
    view: Any,
    success_status: int | None,
    argument_binding: ArgumentBinding,
    unknown_arguments: UnknownArguments,
    on_target_resolved: TargetGuard | None,
) -> DispatchResult:
    guard_many_argument_binding(argument_binding)
    await sync_to_async(call_target_guard, thread_sensitive=True)(
        on_target_resolved, spec, None, user=user, request=request, view=view
    )
    input_context = resolve_input_context(spec, view=view, request=request)
    serializer = await sync_to_async(build_input_serializer_from_data, thread_sensitive=True)(
        params,
        spec.input_serializer,
        partial=spec.partial or False,
        many=True,
        context=input_context,
    )
    data, has_data = resolve_service_many_input(
        spec, serializer, params, unknown_arguments=unknown_arguments
    )
    pool: dict[str, Any] = base_pool(user=user, request=request)
    pool.update(resolve_provider(spec.kwargs, {"view": view, "request": request}))
    if has_data:
        pool["data"] = data
    if serializer is not None:
        pool["serializer"] = serializer
    result: Any = await arun_service_callable(
        spec.service, resolve_callable_kwargs(spec.service, pool), atomic=spec.atomic
    )
    status = (
        success_status
        if success_status is not None
        else resolve_success_status(
            spec.success_status,
            default=200,
            pool={"request": request, "view": view, "result": result},
        )
    )
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
        pool: dict[str, Any] = {
            **base_pool(user=user, request=request),
            **params,
            **view_url_kwargs(view),
        }
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
) -> tuple[Any, bool]:
    """Async :func:`~...dispatch.dispatch_spec._run_output_selector`.

    Same ``(value, is_list)`` contract and ``kind`` semantics. A ``LIST``
    output returns the lazy shaped queryset (the async transport materializes /
    paginates it in a thread, like any list result); a ``RETRIEVE`` output
    awaits ``.afirst()``.
    """
    out_spec = spec.output_selector_spec
    if out_spec is None or out_spec.selector is None:
        return result, False
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
    if out_spec.kind is SelectorKind.LIST:
        return selected, True
    return (await selected.afirst() if is_queryset(selected) else selected), False


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
    pool: dict[str, Any] = {
        **base_pool(user=user, request=request),
        **params,
        **view_url_kwargs(view),
    }
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
