"""``adispatch_spec`` — async sibling of
[`dispatch_spec`][rest_framework_services.dispatch.dispatch_spec.dispatch_spec]."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.core.exceptions import ImproperlyConfigured, ObjectDoesNotExist

from rest_framework_services.dispatch.apply_input_data import apply_input_data
from rest_framework_services.dispatch.base_pool import base_pool
from rest_framework_services.dispatch.utils import (
    COLLECTION_SOURCE,
    INSTANCE_SOURCE,
    OUTPUT_SOURCE,
    SELECTOR_SOURCE,
    acall_preconditions,
    arun_callable,
    arun_off_loop,
    arun_service_callable,
    call_target_guard,
    clear_prefetch_cache,
    guard_many_argument_binding,
    merge_arguments,
    resolve_argument_binding,
    resolve_dispatch_kwargs,
    resolve_input_context,
    resolve_input_data,
    resolve_progress,
    resolve_provider,
    resolve_service_kwargs,
    resolve_service_many_input,
    resolve_unknown_arguments,
    service_input,
    shape_queryset,
    strip_reserved_seeds,
    view_url_kwargs,
    wire_named_errors,
)
from rest_framework_services.selectors.utils import amaterialize_retrieve
from rest_framework_services.types.argument_binding import ArgumentBinding
from rest_framework_services.types.dispatch_result import DispatchResult
from rest_framework_services.types.progress_reporter import ProgressReporter
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.types.target_guard import TargetGuard
from rest_framework_services.types.unknown_arguments import UnknownArguments
from rest_framework_services.types.unset import UNSET
from rest_framework_services.types.view_hooks import ViewHooks
from rest_framework_services.views.mutation.resolve_success_status import resolve_success_status
from rest_framework_services.views.mutation.utils import build_input_serializer_from_data


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
    progress: ProgressReporter | None = None,
    view_hooks: ViewHooks | None = None,
    instance: Any = UNSET,
    filter_data: Mapping[str, Any] | None = None,
) -> DispatchResult:
    """Async
    [`dispatch_spec`][rest_framework_services.dispatch.dispatch_spec.dispatch_spec].

    Identical contract, arguments, policies and
    [`DispatchResult`][rest_framework_services.types.dispatch_result.DispatchResult]
    shape — see the sync twin. What differs is only the execution model: async selectors
    and services are awaited, and sync ones run in Django's thread-sensitive executor so
    the ORM stays safe off the event loop.

    That rule covers **every** callable a spec carries, not just the selector /
    service: ``kwargs`` providers, ``extend_queryset``, ``filter_set``,
    ``input_serializer_context``, a callable ``success_status``,
    ``preconditions``, and the ``on_target_resolved`` guard all run in the
    executor (see
    ``arun_off_loop``). None of them
    can be ``async def`` — a spec is written once for both transports — so any
    that queries would otherwise raise ``SynchronousOnlyOperation`` here and
    nowhere else.

    A ``LIST`` result comes back as the lazy shaped queryset, for the async
    transport to materialize / paginate in a thread.
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
            progress=progress,
            view_hooks=view_hooks,
            instance=instance,
            filter_data=filter_data,
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
            progress=progress,
            view_hooks=view_hooks,
            filter_data=filter_data,
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
    progress: ProgressReporter | None,
    view_hooks: ViewHooks | None,
    filter_data: Mapping[str, Any] | None,
) -> DispatchResult:
    if spec.selector is None:
        raise ImproperlyConfigured("adispatch_spec requires the SelectorSpec to set a `selector`.")
    resolve_unknown_arguments(spec, params, unknown_arguments=unknown_arguments, serializer=None)
    binding = resolve_argument_binding(spec, argument_binding)
    pool: dict[str, Any] = base_pool(
        user=user,
        request=request,
        progress=resolve_progress(
            spec, progress, user=user, request=request, view=view, view_hooks=view_hooks
        ),
    )
    merge_arguments(
        pool,
        binding=binding,
        spread_source=params,
        provider_kwargs=await arun_off_loop(
            resolve_service_kwargs, spec, view=view, request=request, view_hooks=view_hooks
        ),
        url_kwargs=view_url_kwargs(view),
    )
    try:
        result: Any = await arun_callable(
            spec.selector, resolve_dispatch_kwargs(spec.selector, pool)
        )
        # Shaping runs ``extend_queryset`` and the ``filter_set`` (whose
        # validation / ``filter_<name>`` methods query) — all sync, all off-loop.
        result = await arun_off_loop(
            shape_queryset,
            spec,
            result,
            view=view,
            request=request,
            params=filter_data if filter_data is not None else params,
            source_label=SELECTOR_SOURCE,
        )
    except ObjectDoesNotExist:
        if spec.kind is SelectorKind.RETRIEVE:
            return _missing_or_null(spec)
        raise

    # The guard may run ``has_object_permission`` (DB), so keep it off the loop.
    if spec.kind is not SelectorKind.RETRIEVE:
        # LIST: guard the resolved set (per-set / class-level only).
        await arun_off_loop(
            call_target_guard,
            on_target_resolved,
            spec,
            result,
            user=user,
            request=request,
            view=view,
        )
        pool["collection"] = result
        await acall_preconditions(spec, pool)
        return DispatchResult(value=result, kind="list", status=200)
    instance: Any = await amaterialize_retrieve(result)
    if instance is None:
        return _missing_or_null(spec)
    # RETRIEVE: guard the resolved row (object-level permissions run here).
    await arun_off_loop(
        call_target_guard, on_target_resolved, spec, instance, user=user, request=request, view=view
    )
    pool["instance"] = instance
    await acall_preconditions(spec, pool)
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
    progress: ProgressReporter | None,
    view_hooks: ViewHooks | None,
    instance: Any,
    filter_data: Mapping[str, Any] | None,
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
            progress=progress,
            view_hooks=view_hooks,
        )

    if instance is not UNSET:
        # The caller resolved the target itself — the HTTP path, whose
        # ``get_object()`` chain has no off-HTTP meaning and so cannot live here.
        mode, target = "instance", instance
    else:
        mode, target = await _aresolve_target(
            spec, user=user, params=params, request=request, view=view
        )
        if mode == "missing":
            return DispatchResult(value=None, kind="not_found", status=404)
    # The guard may run ``has_object_permission`` (DB), so keep it off the loop.
    await arun_off_loop(
        call_target_guard, on_target_resolved, spec, target, user=user, request=request, view=view
    )
    instance = target if mode == "instance" else None

    # The context provider may run its own (batched) query — off-loop too.
    input_context = await arun_off_loop(
        resolve_input_context, spec, view=view, request=request, view_hooks=view_hooks
    )
    # ``input_data`` providers may query too; same rule.
    params = apply_input_data(
        params,
        await arun_off_loop(
            resolve_input_data,
            spec,
            view=view,
            request=request,
            instance=instance,
            view_hooks=view_hooks,
        ),
    )
    # Validation can touch the DB (e.g. ``UniqueValidator``); run it off-loop.
    serializer = await arun_off_loop(
        build_input_serializer_from_data,
        params,
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
    pool: dict[str, Any] = base_pool(
        user=user,
        request=request,
        progress=resolve_progress(
            spec, progress, user=user, request=request, view=view, view_hooks=view_hooks
        ),
    )
    merge_arguments(
        pool,
        binding=binding,
        spread_source=spread_source,
        provider_kwargs=await arun_off_loop(
            resolve_service_kwargs, spec, view=view, request=request, view_hooks=view_hooks
        ),
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

    with wire_named_errors(serializer):
        await acall_preconditions(spec, pool)
        result: Any = await arun_service_callable(
            spec.service, resolve_dispatch_kwargs(spec.service, pool), atomic=spec.atomic
        )
    # Called directly, not through ``arun_off_loop``: it reads and rebinds one
    # attribute on the target and issues no query, so there is nothing here that
    # the event loop has to be protected from.
    clear_prefetch_cache(instance)
    output_result, output_is_list = await _arun_output_selector(
        spec,
        result,
        user=user,
        request=request,
        view=view,
        params=filter_data if filter_data is not None else params,
    )

    # A callable ``spec.success_status`` keys on the *service's* return value
    # (``result``), captured before the output selector re-fetch replaced it.
    status_pool: dict[str, Any] = {"request": request, "view": view, "result": result}
    if instance is not None:
        status_pool["instance"] = instance
    status = (
        success_status
        if success_status is not None
        # A callable ``success_status`` is user code too: it may read a relation
        # off the result, which is a query.
        else await arun_off_loop(
            resolve_success_status, spec.success_status, default=200, pool=status_pool
        )
    )
    return DispatchResult(
        value=output_result,
        kind="list" if output_is_list else "instance",
        status=status,
        service_result=result,
        instance=instance,
        data=data,
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
    progress: ProgressReporter | None,
    view_hooks: ViewHooks | None,
) -> DispatchResult:
    guard_many_argument_binding(argument_binding)
    await arun_off_loop(
        call_target_guard, on_target_resolved, spec, None, user=user, request=request, view=view
    )
    input_context = await arun_off_loop(
        resolve_input_context, spec, view=view, request=request, view_hooks=view_hooks
    )
    params = apply_input_data(
        params,
        await arun_off_loop(
            resolve_input_data,
            spec,
            view=view,
            request=request,
            instance=None,
            view_hooks=view_hooks,
        ),
    )
    serializer = await arun_off_loop(
        build_input_serializer_from_data,
        params,
        spec.input_serializer,
        partial=spec.partial or False,
        many=True,
        context=input_context,
    )
    data, has_data = resolve_service_many_input(
        spec, serializer, params, unknown_arguments=unknown_arguments
    )
    pool: dict[str, Any] = base_pool(
        user=user,
        request=request,
        progress=resolve_progress(
            spec, progress, user=user, request=request, view=view, view_hooks=view_hooks
        ),
    )
    pool.update(
        await arun_off_loop(
            resolve_service_kwargs, spec, view=view, request=request, view_hooks=view_hooks
        )
    )
    if has_data:
        pool["data"] = data
    if serializer is not None:
        pool["serializer"] = serializer
    # Bulk: once, no target — see the sync sibling.
    with wire_named_errors(serializer):
        await acall_preconditions(spec, pool)
        result: Any = await arun_service_callable(
            spec.service, resolve_dispatch_kwargs(spec.service, pool), atomic=spec.atomic
        )
    status = (
        success_status
        if success_status is not None
        else await arun_off_loop(
            resolve_success_status,
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
            # No live reporter, deliberately: a lookup has no progress to report, and
            # one emitting *after* the service finished reads to a watching client as
            # the work having restarted. These take the no-op ``base_pool`` supplies.
            **base_pool(user=user, request=request),
            # Reserved seeds stripped from the client spread, as ``merge_arguments``
            # does elsewhere: otherwise a caller sending ``{"user": …}`` outranks the
            # dispatcher in the pool deciding *which row* is mutated.
            **strip_reserved_seeds(params),
            **view_url_kwargs(view),
        }
        pool.update(
            await arun_off_loop(
                resolve_provider, coll_spec.kwargs, {"view": view, "request": request}
            )
        )
        result: Any = await arun_callable(
            coll_spec.selector, resolve_dispatch_kwargs(coll_spec.selector, pool)
        )
        collection = await arun_off_loop(
            shape_queryset,
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

    Same ``(value, is_list)`` contract and ``kind`` semantics; a ``LIST`` output
    stays the lazy shaped queryset and a ``RETRIEVE`` awaits ``.afirst()``.
    """
    out_spec = spec.output_selector_spec
    if out_spec is None or out_spec.selector is None:
        return result, False
    pool: dict[str, Any] = {
        # No live reporter, deliberately: a lookup has no progress to report, and
        # one emitting *after* the service finished reads to a watching client as
        # the work having restarted. These take the no-op ``base_pool`` supplies.
        **base_pool(user=user, request=request),
        "instance": result,
        "result": result,
    }
    selected: Any = await arun_callable(
        out_spec.selector, resolve_dispatch_kwargs(out_spec.selector, pool)
    )
    selected = await arun_off_loop(
        shape_queryset,
        out_spec,
        selected,
        view=view,
        request=request,
        params=params,
        source_label=OUTPUT_SOURCE,
    )
    if out_spec.kind is SelectorKind.LIST:
        return selected, True
    return (await amaterialize_retrieve(selected)), False


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
        # No live reporter, deliberately: a lookup has no progress to report, and
        # one emitting *after* the service finished reads to a watching client as
        # the work having restarted. These take the no-op ``base_pool`` supplies.
        **base_pool(user=user, request=request),
        # Reserved seeds stripped from the client spread, as ``merge_arguments``
        # does elsewhere: otherwise a caller sending ``{"user": …}`` outranks the
        # dispatcher in the pool deciding *which row* is mutated.
        **strip_reserved_seeds(params),
        **view_url_kwargs(view),
    }
    pool.update(
        await arun_off_loop(
            resolve_provider, instance_spec.kwargs, {"view": view, "request": request}
        )
    )
    try:
        result: Any = await arun_callable(
            instance_spec.selector, resolve_dispatch_kwargs(instance_spec.selector, pool)
        )
        result = await arun_off_loop(
            shape_queryset,
            instance_spec,
            result,
            view=view,
            request=request,
            params=params,
            source_label=INSTANCE_SOURCE,
        )
    except ObjectDoesNotExist:
        return (False, None)
    instance: Any = await amaterialize_retrieve(result)
    if instance is None:
        return (False, None)
    return (True, instance)


__all__ = ["adispatch_spec"]
