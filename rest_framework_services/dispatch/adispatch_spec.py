"""``adispatch_spec`` — async sibling of
[`dispatch_spec`][rest_framework_services.dispatch.dispatch_spec.dispatch_spec]."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.core.exceptions import ImproperlyConfigured, ObjectDoesNotExist

from rest_framework_services.dispatch.aenforce_affordances import aenforce_affordances
from rest_framework_services.dispatch.apply_input_data import apply_input_data
from rest_framework_services.dispatch.base_pool import base_pool
from rest_framework_services.dispatch.utils import (
    COLLECTION_SOURCE,
    INSTANCE_SOURCE,
    NOTHING_FILLABLE,
    OUTPUT_SOURCE,
    SELECTOR_SOURCE,
    acall_preconditions,
    arun_callable,
    arun_off_loop,
    arun_service_callable,
    call_target_guard,
    caller_fillable,
    clear_prefetch_cache,
    guard_many_argument_binding,
    guard_mapping_params,
    many_argument_errors,
    many_argument_items,
    merge_arguments,
    refuse_arguments_beside_many,
    resolve_argument_binding,
    resolve_dispatch_kwargs,
    resolve_input_context,
    resolve_input_data,
    resolve_progress,
    resolve_provider,
    resolve_service_kwargs,
    resolve_service_many_input,
    resolve_unknown_arguments,
    server_owned_keys,
    service_extras,
    service_input,
    service_return_as_list,
    shape_queryset,
    strip_hidden_inputs,
    strip_reserved_seeds,
    view_url_kwargs,
    wire_named_errors,
)
from rest_framework_services.selectors.utils import amaterialize_retrieve
from rest_framework_services.types.argument_binding import ArgumentBinding
from rest_framework_services.types.dispatch_result import DispatchResult
from rest_framework_services.types.pool_seeds import DEFAULT_POOL_SEEDS, PoolSeeds
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
    pool_seeds: PoolSeeds = DEFAULT_POOL_SEEDS,
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
    many_as_argument: bool = False,
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
    ``input_data``, ``input_serializer_context``, the ``progress_reporter``
    provider, a callable ``success_status``, ``preconditions``, and the
    ``on_target_resolved`` guard all run in the executor (see
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
            pool_seeds=pool_seeds,
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
            many_as_argument=many_as_argument,
        )
    if isinstance(spec, SelectorSpec):
        return await _adispatch_selector(
            spec,
            user=user,
            pool_seeds=pool_seeds,
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
    pool_seeds: PoolSeeds,
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
    resolve_unknown_arguments(
        spec,
        params,
        unknown_arguments=unknown_arguments,
        serializer=None,
        reserved=pool_seeds.reserved,
    )
    binding = resolve_argument_binding(spec, argument_binding)
    owned = server_owned_keys(spec)
    pool: dict[str, Any] = base_pool(
        seeds=pool_seeds,
        user=user,
        request=request,
        # The spec's ``progress_reporter`` is a provider, and building a sink for
        # a task record or an audit row is a query — off the loop like the others.
        progress=await arun_off_loop(
            resolve_progress,
            spec,
            progress,
            user=user,
            request=request,
            view=view,
            view_hooks=view_hooks,
        ),
    )
    merge_arguments(
        pool,
        binding=binding,
        reserved=pool_seeds.reserved,
        # A key the selector or a precondition marks ``NotClientInput`` leaves the
        # caller's input before the spread, so neither the binding's precedence nor
        # a provider declining with ``UNSET`` can let the caller's value back in.
        spread_source=strip_hidden_inputs(params, owned),
        provider_kwargs=await arun_off_loop(
            resolve_service_kwargs, spec, view=view, request=request, view_hooks=view_hooks
        ),
        url_kwargs=view_url_kwargs(view, reserved=pool_seeds.reserved),
    )
    fillable = caller_fillable(spec, params, owned=owned)
    try:
        result: Any = await arun_callable(
            spec.selector, resolve_dispatch_kwargs(spec.selector, pool, fillable=fillable)
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
            pool=pool,
            reserved=pool_seeds.reserved,
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
        await acall_preconditions(spec, pool, fillable=fillable)
        return DispatchResult(value=result, kind="list", status=200)
    instance: Any = await amaterialize_retrieve(result)
    if instance is None:
        return _missing_or_null(spec)
    # RETRIEVE: guard the resolved row (object-level permissions run here).
    await arun_off_loop(
        call_target_guard, on_target_resolved, spec, instance, user=user, request=request, view=view
    )
    pool["instance"] = instance
    await acall_preconditions(spec, pool, fillable=fillable)
    return DispatchResult(value=instance, kind="instance", status=200)


def _missing_or_null(spec: SelectorSpec[Any, Any]) -> DispatchResult:
    if spec.allow_none:
        return DispatchResult(value=None, kind="instance", status=200)
    return DispatchResult(value=None, kind="not_found", status=404)


async def _adispatch_service(
    spec: ServiceSpec[Any, Any, Any],
    *,
    user: Any,
    pool_seeds: PoolSeeds,
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
    many_as_argument: bool,
) -> DispatchResult:
    if spec.many:
        return await _adispatch_service_many(
            spec,
            user=user,
            pool_seeds=pool_seeds,
            params=params,
            request=request,
            view=view,
            success_status=success_status,
            argument_binding=argument_binding,
            unknown_arguments=unknown_arguments,
            on_target_resolved=on_target_resolved,
            progress=progress,
            view_hooks=view_hooks,
            many_as_argument=many_as_argument,
        )
    guard_mapping_params(params)
    owned = server_owned_keys(spec)

    if instance is not UNSET:
        # The caller resolved the target itself — the HTTP path, whose
        # ``get_object()`` chain has no off-HTTP meaning and so cannot live here.
        mode, target = "instance", instance
    else:
        mode, target = await _aresolve_target(
            spec,
            user=user,
            pool_seeds=pool_seeds,
            params=params,
            request=request,
            view=view,
            filter_data=filter_data,
            owned=owned,
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
    server_input = await arun_off_loop(
        resolve_input_data,
        spec,
        view=view,
        request=request,
        instance=instance,
        view_hooks=view_hooks,
    )
    params = apply_input_data(params, server_input)
    # Validation can touch the DB (e.g. ``UniqueValidator``); run it off-loop.
    serializer = await arun_off_loop(
        build_input_serializer_from_data,
        params,
        spec.input_serializer,
        partial=spec.partial or False,
        context=input_context,
        instance=instance,
    )
    # The policy sees a server-owned key first, so ``REJECT`` on a closed spec
    # still refuses it; only what the service would be handed loses it. With no
    # input serializer under a spreading binding, that is the caller's values for
    # the service's own parameters as well as what ``PASSTHROUGH`` forwards. A key
    # ``input_data`` wrote holds the server's value, so both strips keep it. Each
    # strip takes the whole call's server-owned keys, not the service's alone:
    # test_a_key_another_callable_hides_never_reaches_the_services_data holds this
    # one, test_a_field_named_like_a_key_a_precondition_hides_is_not_spread the next.
    extras = dict(
        strip_hidden_inputs(
            service_extras(
                spec,
                params,
                resolve_unknown_arguments(
                    spec,
                    params,
                    unknown_arguments=unknown_arguments,
                    serializer=serializer,
                    reserved=pool_seeds.reserved,
                    argument_binding=argument_binding,
                ),
                argument_binding=argument_binding,
                reserved=pool_seeds.reserved,
            ),
            owned,
            server_supplied=server_input,
        )
    )
    data, spread_source = service_input(serializer, extras)

    binding = resolve_argument_binding(spec, argument_binding)
    pool: dict[str, Any] = base_pool(
        seeds=pool_seeds,
        user=user,
        request=request,
        progress=await arun_off_loop(
            resolve_progress,
            spec,
            progress,
            user=user,
            request=request,
            view=view,
            view_hooks=view_hooks,
        ),
    )
    merge_arguments(
        pool,
        binding=binding,
        reserved=pool_seeds.reserved,
        # A field the input serializer declares stays in ``data``, which is that
        # serializer's payload; it fills a hidden parameter only when ``input_data``
        # supplied it, so that the value validated is the server's.
        spread_source=strip_hidden_inputs(spread_source, owned, server_supplied=server_input),
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

    await aenforce_affordances(spec, pool, instance=instance, reserved=pool_seeds.reserved)
    fillable = caller_fillable(
        spec,
        params,
        owned=owned,
        serializer=serializer,
        argument_binding=argument_binding,
        reserved=pool_seeds.reserved,
    )
    with wire_named_errors(serializer):
        await acall_preconditions(spec, pool, fillable=fillable)
        result: Any = await arun_service_callable(
            spec.service,
            resolve_dispatch_kwargs(spec.service, pool, fillable=fillable),
            atomic=spec.atomic,
        )
    # Called directly, not through ``arun_off_loop``: it reads and rebinds one
    # attribute on the target and issues no query, so there is nothing here that
    # the event loop has to be protected from.
    clear_prefetch_cache(instance)
    output_result, output_is_list = await _arun_output_selector(
        spec,
        result,
        user=user,
        pool_seeds=pool_seeds,
        request=request,
        view=view,
        filter_data=filter_data,
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
    pool_seeds: PoolSeeds,
    params: Any,
    request: Any,
    view: Any,
    success_status: int | None,
    argument_binding: ArgumentBinding,
    unknown_arguments: UnknownArguments,
    on_target_resolved: TargetGuard | None,
    progress: ProgressReporter | None,
    view_hooks: ViewHooks | None,
    many_as_argument: bool,
) -> DispatchResult:
    guard_many_argument_binding(argument_binding)
    await arun_off_loop(
        call_target_guard, on_target_resolved, spec, None, user=user, request=request, view=view
    )
    input_context = await arun_off_loop(
        resolve_input_context, spec, view=view, request=request, view_hooks=view_hooks
    )
    input_data = await arun_off_loop(
        resolve_input_data,
        spec,
        view=view,
        request=request,
        instance=None,
        view_hooks=view_hooks,
    )
    # See the sync sibling for the order: validation, then the arguments beside the list.
    argument: str | None = spec.many_argument if many_as_argument else None
    items: Any = apply_input_data(
        many_argument_items(spec, params) if many_as_argument else params, input_data
    )
    with many_argument_errors(argument):
        serializer = await arun_off_loop(
            build_input_serializer_from_data,
            items,
            spec.input_serializer,
            partial=spec.partial or False,
            many=True,
            context=input_context,
        )
    if many_as_argument:
        refuse_arguments_beside_many(spec, params, reserved=pool_seeds.reserved)
    with many_argument_errors(argument):
        data, has_data = resolve_service_many_input(
            spec,
            serializer,
            items,
            unknown_arguments=unknown_arguments,
            reserved=pool_seeds.reserved,
            index_errors=many_as_argument,
        )
    pool: dict[str, Any] = base_pool(
        seeds=pool_seeds,
        user=user,
        request=request,
        progress=await arun_off_loop(
            resolve_progress,
            spec,
            progress,
            user=user,
            request=request,
            view=view,
            view_hooks=view_hooks,
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
    await aenforce_affordances(spec, pool, instance=None, reserved=pool_seeds.reserved)
    # Always ``BUNDLE``: see the sync sibling.
    with wire_named_errors(serializer):
        await acall_preconditions(spec, pool, fillable=NOTHING_FILLABLE)
        result: Any = await arun_service_callable(
            spec.service,
            resolve_dispatch_kwargs(spec.service, pool, fillable=NOTHING_FILLABLE),
            atomic=spec.atomic,
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
    pool_seeds: PoolSeeds,
    params: Mapping[str, Any],
    request: Any,
    view: Any,
    filter_data: Mapping[str, Any] | None,
    owned: frozenset[str],
) -> tuple[str, Any]:
    """Async :func:`~...dispatch.dispatch_spec._resolve_target`.

    Same split — ``params`` is the nested selector's argument channel and
    ``filter_data`` the one its ``filter_set`` reads; see the sync sibling for
    why a scoping filter on a nested target spec needs it.
    """
    filters = params if filter_data is None else filter_data
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
            **base_pool(user=user, request=request, seeds=pool_seeds),
            # Reserved seeds stripped from the client spread, as ``merge_arguments``
            # does elsewhere: otherwise a caller sending ``{"user": …}`` outranks the
            # dispatcher in the pool deciding *which row* is mutated.
            # And a server-owned name, which any callable in the call may have
            # marked ``NotClientInput``: the caller never fills one, while the
            # route capture below and the provider still can.
            **strip_hidden_inputs(
                strip_reserved_seeds(params, reserved=pool_seeds.reserved), owned
            ),
            **view_url_kwargs(view, reserved=pool_seeds.reserved),
        }
        pool.update(
            await arun_off_loop(
                resolve_provider, coll_spec.kwargs, {"view": view, "request": request}
            )
        )
        result: Any = await arun_callable(
            coll_spec.selector,
            resolve_dispatch_kwargs(
                coll_spec.selector, pool, fillable=caller_fillable(coll_spec, params, owned=owned)
            ),
        )
        collection = await arun_off_loop(
            shape_queryset,
            coll_spec,
            result,
            view=view,
            request=request,
            params=filters,
            source_label=COLLECTION_SOURCE,
            pool=pool,
            reserved=pool_seeds.reserved,
        )
        return ("collection", collection)
    found, instance = await _aresolve_instance(
        spec,
        user=user,
        pool_seeds=pool_seeds,
        params=params,
        request=request,
        view=view,
        filter_data=filters,
        owned=owned,
    )
    return ("instance", instance) if found else ("missing", None)


async def _arun_output_selector(
    spec: ServiceSpec[Any, Any, Any],
    result: Any,
    *,
    user: Any,
    pool_seeds: PoolSeeds,
    request: Any,
    view: Any,
    filter_data: Mapping[str, Any] | None,
) -> tuple[Any, bool]:
    """Async :func:`~...dispatch.dispatch_spec._run_output_selector`.

    Same ``(value, is_list)`` contract and ``kind`` semantics; a ``LIST`` output
    stays the lazy shaped queryset and a ``RETRIEVE`` awaits ``.afirst()``. With no
    ``selector`` the service's own return is presented, as a list under ``LIST``.
    Its ``filter_set`` reads ``filter_data`` alone, bound to an empty mapping without
    one; see the sync sibling for why the call's arguments are not a fallback.
    """
    out_spec = spec.output_selector_spec
    if out_spec is None:
        return result, False
    if out_spec.selector is None:
        # Held by test_the_services_own_return_is_presented_as_a_list (the ``LIST``
        # arm) and test_a_retrieve_declaration_with_no_re_read_is_still_one_value.
        if out_spec.kind is SelectorKind.LIST:
            return service_return_as_list(spec, result), True
        return result, False
    pool: dict[str, Any] = {
        # No live reporter, deliberately: a lookup has no progress to report, and
        # one emitting *after* the service finished reads to a watching client as
        # the work having restarted. These take the no-op ``base_pool`` supplies.
        **base_pool(user=user, request=request, seeds=pool_seeds),
        "instance": result,
        "result": result,
    }
    # Not refused when a parameter is unfilled, for the reason an affordance
    # condition is not: no client input reaches this pool, so naming one would ask
    # the caller for a value they cannot send. And the write has committed by now,
    # so a validation error would tell the caller nothing happened, and a caller
    # that retries on one would write twice. The ``TypeError`` is the author's.
    selected: Any = await arun_callable(
        out_spec.selector,
        resolve_dispatch_kwargs(out_spec.selector, pool, fillable=NOTHING_FILLABLE),
    )
    selected = await arun_off_loop(
        shape_queryset,
        out_spec,
        selected,
        view=view,
        request=request,
        params=filter_data if filter_data is not None else {},
        source_label=OUTPUT_SOURCE,
        pool=pool,
        reserved=pool_seeds.reserved,
    )
    if out_spec.kind is SelectorKind.LIST:
        return selected, True
    return (await amaterialize_retrieve(selected)), False


async def _aresolve_instance(
    spec: ServiceSpec[Any, Any, Any],
    *,
    user: Any,
    pool_seeds: PoolSeeds,
    params: Mapping[str, Any],
    request: Any,
    view: Any,
    filter_data: Mapping[str, Any],
    owned: frozenset[str],
) -> tuple[bool, Any]:
    instance_spec = spec.instance_selector_spec
    if instance_spec is None or instance_spec.selector is None:
        return (True, None)
    pool: dict[str, Any] = {
        # No live reporter, deliberately: a lookup has no progress to report, and
        # one emitting *after* the service finished reads to a watching client as
        # the work having restarted. These take the no-op ``base_pool`` supplies.
        **base_pool(user=user, request=request, seeds=pool_seeds),
        # Reserved seeds stripped from the client spread, as ``merge_arguments``
        # does elsewhere: otherwise a caller sending ``{"user": …}`` outranks the
        # dispatcher in the pool deciding *which row* is mutated.
        # And a server-owned name, which any callable in the call may have marked
        # ``NotClientInput``: the caller never fills one, while the route capture
        # below and the provider still can.
        **strip_hidden_inputs(strip_reserved_seeds(params, reserved=pool_seeds.reserved), owned),
        **view_url_kwargs(view, reserved=pool_seeds.reserved),
    }
    pool.update(
        await arun_off_loop(
            resolve_provider, instance_spec.kwargs, {"view": view, "request": request}
        )
    )
    try:
        result: Any = await arun_callable(
            instance_spec.selector,
            resolve_dispatch_kwargs(
                instance_spec.selector,
                pool,
                fillable=caller_fillable(instance_spec, params, owned=owned),
            ),
        )
        result = await arun_off_loop(
            shape_queryset,
            instance_spec,
            result,
            view=view,
            request=request,
            params=filter_data,
            source_label=INSTANCE_SOURCE,
            pool=pool,
            reserved=pool_seeds.reserved,
        )
    except ObjectDoesNotExist:
        return (False, None)
    instance: Any = await amaterialize_retrieve(result)
    if instance is None:
        return (False, None)
    return (True, instance)


__all__ = ["adispatch_spec"]
