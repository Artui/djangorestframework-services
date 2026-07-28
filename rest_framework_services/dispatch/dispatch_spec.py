"""``dispatch_spec`` — transport-neutral execution of a Service/Selector spec."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.core.exceptions import ImproperlyConfigured, ObjectDoesNotExist

from rest_framework_services.dispatch.utils import (
    COLLECTION_SOURCE,
    INSTANCE_SOURCE,
    OUTPUT_SOURCE,
    SELECTOR_SOURCE,
    base_pool,
    call_target_guard,
    guard_many_argument_binding,
    merge_arguments,
    resolve_argument_binding,
    resolve_dispatch_kwargs,
    resolve_input_context,
    resolve_provider,
    resolve_service_many_input,
    resolve_unknown_arguments,
    service_input,
    shape_queryset,
    view_url_kwargs,
)
from rest_framework_services.selectors.utils import is_queryset, run_selector
from rest_framework_services.services.run_service import run_service
from rest_framework_services.types.argument_binding import ArgumentBinding
from rest_framework_services.types.dispatch_result import DispatchResult
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.types.target_guard import TargetGuard
from rest_framework_services.types.unknown_arguments import UnknownArguments
from rest_framework_services.views.mutation.resolve_success_status import resolve_success_status
from rest_framework_services.views.mutation.utils import build_input_serializer_from_data


def dispatch_spec(
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
    """Execute ``spec`` without a DRF view, returning a :class:`DispatchResult`.

    The single transport-neutral execution path: an HTTP view, the MCP server,
    or any other caller hands the **flat** ``params`` mapping (the role
    ``request.data`` / ``query_params`` / URL kwargs play on HTTP) plus the
    acting ``user``, and gets back the resolved domain value to format for its
    wire. Composes the blessed dispatch leaves; no pagination, ordering, or
    output rendering happens here (those are transport concerns — render the
    result with :func:`~rest_framework_services.render_spec_output`).

    - A :class:`ServiceSpec` runs the mutation flow: resolve the target via
      ``instance_selector_spec`` (from ``params``) → validate ``input_serializer``
      → run the service → re-fetch through ``output_selector_spec`` → result.
      A missing instance yields ``kind="not_found"``.
    - A :class:`SelectorSpec` runs the read flow: invoke the selector → apply
      queryset shaping (``select_related`` … ``filter_set``, with ``params`` as
      the filter data) → for ``RETRIEVE`` materialize via ``.first()`` and honour
      ``allow_none`` / not-found; ``LIST`` returns the shaped + filtered queryset.

    ``request`` / ``view`` are optional and only forwarded to user callables
    that declare them (``extend_queryset``, the context providers, ``kwargs``);
    a pure non-HTTP caller passes neither. The ``view``'s ``kwargs`` (a route's
    captures, seeded by ``build_offline_context(kwargs=…)``) are additionally
    **spread into the selector / target pools** — the off-HTTP counterpart of the
    HTTP ``extra_url_kwargs=view.kwargs``, authoritative over ``params`` on a
    conflict, below the ``spec.kwargs`` provider — so a selector reading a URL
    kwarg from its extras works off-HTTP. ``success_status`` overrides the
    mutation status hint (else ``spec.success_status`` or ``200``).

    Three caller-side policies tune how the wire maps onto the spec; the
    defaults reproduce the pre-policy behaviour exactly:

    - ``argument_binding`` (:class:`ArgumentBinding`) — whether client input
      lands as a single ``data`` bundle or is spread as individual kwargs, and
      how it ranks against the author's ``kwargs``. ``AUTO`` resolves per spec
      type (service → bundle, selector → spread).
    - ``unknown_arguments`` (:class:`UnknownArguments`) — strictness about
      ``params`` keys outside the spec's declared set: ``IGNORE`` (drop),
      ``REJECT`` (raise), ``PASSTHROUGH`` (forward to the callable).
    - ``on_target_resolved`` (:class:`TargetGuard`) — a hook invoked with the
      resolved mutation target before the service runs. Pass
      :func:`~rest_framework_services.enforce_permissions` directly to enforce
      object-level permissions; ``dispatch_spec`` itself stays authz-agnostic.

    On a ``many=True`` bulk spec ``unknown_arguments`` is honoured **per list
    element** (``REJECT`` raises on the first item with an undeclared key,
    ``PASSTHROUGH`` folds each item's extras into its data, ``IGNORE`` drops
    them). ``argument_binding`` has no meaning there — the service receives the
    whole list as one ``data`` argument, so there is nothing to spread — and a
    non-default binding with ``many=True`` raises ``ValueError`` rather than
    being silently ignored.
    """
    if isinstance(spec, ServiceSpec):
        return _dispatch_service(
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
        return _dispatch_selector(
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
        f"dispatch_spec expects a ServiceSpec or SelectorSpec; got {type(spec).__name__}."
    )


def _dispatch_selector(
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
        raise ImproperlyConfigured(
            "dispatch_spec requires the SelectorSpec to set a `selector` — there "
            "is no view `get_queryset()` / `get_object()` fallback off the HTTP path."
        )
    # A selector has no validation step, so its params already flow through the
    # spread untouched; only ``REJECT`` has anything to do here (it raises).
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
        result: Any = run_selector(spec.selector, resolve_dispatch_kwargs(spec.selector, pool))
        result = shape_queryset(
            spec, result, view=view, request=request, params=params, source_label=SELECTOR_SOURCE
        )
    except ObjectDoesNotExist:
        if spec.kind is SelectorKind.RETRIEVE:
            return _missing_or_null(spec)
        raise

    if spec.kind is not SelectorKind.RETRIEVE:
        # LIST: guard the resolved set (per-set / class-level only — the queryset
        # is not a Model, so fix (a) skips has_object_permission).
        call_target_guard(on_target_resolved, spec, result, user=user, request=request, view=view)
        return DispatchResult(value=result, kind="list", status=200)
    instance: Any = result.first() if is_queryset(result) else result
    if instance is None:
        return _missing_or_null(spec)
    # RETRIEVE: guard the resolved row (object-level permissions run here).
    call_target_guard(on_target_resolved, spec, instance, user=user, request=request, view=view)
    return DispatchResult(value=instance, kind="instance", status=200)


def _missing_or_null(spec: SelectorSpec[Any, Any]) -> DispatchResult:
    """A RETRIEVE that resolved nothing: 200 + ``None`` (allow_none) else 404."""
    if spec.allow_none:
        return DispatchResult(value=None, kind="instance", status=200)
    return DispatchResult(value=None, kind="not_found", status=404)


def _dispatch_service(
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
        return _dispatch_service_many(
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

    mode, target = _resolve_target(spec, user=user, params=params, request=request, view=view)
    if mode == "missing":
        return DispatchResult(value=None, kind="not_found", status=404)
    call_target_guard(on_target_resolved, spec, target, user=user, request=request, view=view)
    instance = target if mode == "instance" else None

    input_context = resolve_input_context(spec, view=view, request=request)
    serializer = build_input_serializer_from_data(
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

    result: Any = run_service(
        spec.service, resolve_dispatch_kwargs(spec.service, pool), atomic=spec.atomic
    )
    output_result, output_is_list = _run_output_selector(
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


def _dispatch_service_many(
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
    """Bulk list-payload: ``params`` is the array; the service gets the list."""
    guard_many_argument_binding(argument_binding)
    call_target_guard(on_target_resolved, spec, None, user=user, request=request, view=view)
    input_context = resolve_input_context(spec, view=view, request=request)
    serializer = build_input_serializer_from_data(
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
    result: Any = run_service(
        spec.service, resolve_dispatch_kwargs(spec.service, pool), atomic=spec.atomic
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


def _resolve_target(
    spec: ServiceSpec[Any, Any, Any],
    *,
    user: Any,
    params: Mapping[str, Any],
    request: Any,
    view: Any,
) -> tuple[str, Any]:
    """Resolve the mutation target: ``("collection", qs)``, ``("instance", obj)``,
    or ``("missing", None)`` when a required instance wasn't found.

    A ``collection_selector_spec`` (bulk) takes precedence; an empty collection
    is a valid no-op (never ``"missing"``).
    """
    coll_spec = spec.collection_selector_spec
    if coll_spec is not None:
        return (
            "collection",
            _resolve_collection(coll_spec, user=user, params=params, request=request, view=view),
        )
    found, instance = _resolve_instance(spec, user=user, params=params, request=request, view=view)
    return ("instance", instance) if found else ("missing", None)


def _resolve_collection(
    coll_spec: SelectorSpec[Any, Any],
    *,
    user: Any,
    params: Mapping[str, Any],
    request: Any,
    view: Any,
) -> Any:
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
    result: Any = run_selector(
        coll_spec.selector, resolve_dispatch_kwargs(coll_spec.selector, pool)
    )
    return shape_queryset(
        coll_spec, result, view=view, request=request, params=params, source_label=COLLECTION_SOURCE
    )


def _run_output_selector(
    spec: ServiceSpec[Any, Any, Any],
    result: Any,
    *,
    user: Any,
    request: Any,
    view: Any,
    params: Mapping[str, Any],
) -> tuple[Any, bool]:
    """Re-fetch + shape the service result through ``output_selector_spec``.

    Returns ``(value, is_list)``. ``output_selector_spec.kind`` drives the
    cardinality: ``RETRIEVE`` (the default) collapses a queryset to a single
    instance via ``.first()`` (``is_list`` ``False``); ``LIST`` — valid only
    alongside ``collection_selector_spec`` — returns the shaped set untouched
    (``is_list`` ``True``) so the transport renders it ``many=True``. With no
    output selector the service return passes through as a single value.
    """
    out_spec = spec.output_selector_spec
    if out_spec is None or out_spec.selector is None:
        return result, False
    # The nested spec's own kwargs / permissions are ignored (the surrounding
    # mutation owns them); the service return joins the pool as ``result`` /
    # ``instance``.
    pool: dict[str, Any] = {
        **base_pool(user=user, request=request),
        "instance": result,
        "result": result,
    }
    selected: Any = run_selector(
        out_spec.selector, resolve_dispatch_kwargs(out_spec.selector, pool)
    )
    selected = shape_queryset(
        out_spec, selected, view=view, request=request, params=params, source_label=OUTPUT_SOURCE
    )
    if out_spec.kind is SelectorKind.LIST:
        return selected, True
    return (selected.first() if is_queryset(selected) else selected), False


def _resolve_instance(
    spec: ServiceSpec[Any, Any, Any],
    *,
    user: Any,
    params: Mapping[str, Any],
    request: Any,
    view: Any,
) -> tuple[bool, Any]:
    """Resolve the mutation target from ``instance_selector_spec`` + ``params``.

    Returns ``(found, instance)``. ``(True, None)`` when no instance is
    configured (a create); ``(False, None)`` when the lookup matched nothing.
    """
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
        result: Any = run_selector(
            instance_spec.selector, resolve_dispatch_kwargs(instance_spec.selector, pool)
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
    instance: Any = result.first() if is_queryset(result) else result
    if instance is None:
        return (False, None)
    return (True, instance)


__all__ = ["dispatch_spec"]
