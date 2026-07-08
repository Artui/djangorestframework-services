"""Internal helpers shared by the transport-neutral dispatch + render surface.

Nothing here is exported from the package's public API; the public entry
points are :func:`~rest_framework_services.dispatch_spec`,
:func:`~rest_framework_services.adispatch_spec`, and
:func:`~rest_framework_services.render_spec_output`.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from asgiref.sync import sync_to_async
from rest_framework.exceptions import ValidationError

from rest_framework_services.is_async import is_async
from rest_framework_services.selectors.utils import apply_queryset_shaping
from rest_framework_services.services.arun_service import arun_service
from rest_framework_services.services.run_service import run_service
from rest_framework_services.types.argument_binding import ArgumentBinding
from rest_framework_services.types.offline_context import OfflineContext
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.types.target_guard import TargetGuard
from rest_framework_services.types.unknown_arguments import UnknownArguments
from rest_framework_services.views.utils import resolve_callable_kwargs

# Labels handed to ``apply_queryset_shaping`` so a misconfiguration points at
# the offending spec field.
SELECTOR_SOURCE = "SelectorSpec.selector"
INSTANCE_SOURCE = "ServiceSpec.instance_selector_spec.selector"
COLLECTION_SOURCE = "ServiceSpec.collection_selector_spec.selector"
OUTPUT_SOURCE = "ServiceSpec.output_selector_spec.selector"

# Pool keys carrying transport-controlled seeds. A client-supplied argument
# named after one of these would override the dispatcher's authoritative value
# (a credential-spoofing footgun), so the ``SPREAD_*`` argument-binding modes
# strip them from the spread. The dispatched callable may still *declare* a
# parameter of that name — it receives the seed, the documented idiom.
RESERVED_POOL_SEEDS: frozenset[str] = frozenset(
    {"request", "user", "data", "serializer", "instance", "collection"}
)


def base_pool(*, user: Any, request: Any) -> dict[str, Any]:
    """The two seeds every dispatched callable's pool carries."""
    return {"request": request, "user": user}


def resolve_argument_binding(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any],
    argument_binding: ArgumentBinding,
) -> ArgumentBinding:
    """Resolve ``AUTO`` to the per-spec-type default; pass any other mode through.

    ``AUTO`` reproduces the pre-policy behaviour: a :class:`ServiceSpec` takes
    its payload as one ``data`` bundle (``BUNDLE``); a :class:`SelectorSpec`
    spreads its params with the author's ``kwargs`` winning conflicts
    (``SPREAD_AUTHOR_WINS``).
    """
    if argument_binding is not ArgumentBinding.AUTO:
        return argument_binding
    if isinstance(spec, ServiceSpec):
        return ArgumentBinding.BUNDLE
    return ArgumentBinding.SPREAD_AUTHOR_WINS


def merge_arguments(
    pool: dict[str, Any],
    *,
    binding: ArgumentBinding,
    spread_source: Mapping[str, Any],
    provider_kwargs: dict[str, Any],
) -> None:
    """Merge spread args + the ``spec.kwargs`` provider into ``pool`` per ``binding``.

    ``binding`` must already be resolved (never ``AUTO``). ``BUNDLE`` spreads
    nothing — only the provider's keys join the pool. The ``SPREAD_*`` modes
    spread ``spread_source`` (with the reserved seeds stripped) and differ only
    in precedence against the provider: ``SPREAD_AUTHOR_WINS`` lets the provider
    override the spread, ``SPREAD_CALLER_WINS`` lets the spread override the
    provider.
    """
    if binding is ArgumentBinding.BUNDLE:
        pool.update(provider_kwargs)
        return
    spread = {k: v for k, v in spread_source.items() if k not in RESERVED_POOL_SEEDS}
    if binding is ArgumentBinding.SPREAD_AUTHOR_WINS:
        pool.update(spread)
        pool.update(provider_kwargs)
    else:  # SPREAD_CALLER_WINS
        pool.update(provider_kwargs)
        pool.update(spread)


def _callable_param_names(fn: Callable[..., Any]) -> set[str] | None:
    """Declared keyword-acceptable parameter names of ``fn``; ``None`` if open.

    ``None`` (open) when ``fn`` declares ``**kwargs`` — it accepts anything, so
    no key can be called "unknown" (mirrors :func:`resolve_callable_kwargs`).
    """
    parameters = inspect.signature(fn).parameters
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
        return None
    return {
        name
        for name, p in parameters.items()
        if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    }


def _selector_consumed_keys(sel_spec: SelectorSpec[Any, Any] | None) -> set[str] | None:
    """Params keys a nested target selector consumes; ``None`` if open.

    Open when the selector has a duck-typed ``filter_set`` (its fields are
    opaque to the core) or declares ``**kwargs``.
    """
    if sel_spec is None or sel_spec.selector is None:
        return set()
    if sel_spec.filter_set is not None:
        return None
    return _callable_param_names(sel_spec.selector)


def declared_input_keys(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any],
    *,
    serializer: Any,
) -> set[str] | None:
    """The set of ``params`` keys ``spec`` declares as input, or ``None`` if open.

    Derived from the spec alone — no transport knowledge. A :class:`ServiceSpec`
    declares its ``input_serializer`` fields plus the keys its nested target
    selectors consume (e.g. the ``pk`` an ``instance_selector_spec`` reads). A
    :class:`SelectorSpec` declares its ``selector``'s parameters. ``None`` means
    the set can't be enumerated (a ``**kwargs`` callable or a duck-typed
    ``filter_set``), so there is nothing to flag as unknown.
    """
    if isinstance(spec, SelectorSpec):
        if spec.filter_set is not None:
            return None
        return _callable_param_names(spec.selector) if spec.selector is not None else set()
    declared: set[str] = set(serializer.fields) if serializer is not None else set()
    for nested in (spec.instance_selector_spec, spec.collection_selector_spec):
        consumed = _selector_consumed_keys(nested)
        if consumed is None:
            return None
        declared |= consumed
    return declared


def resolve_unknown_arguments(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any],
    params: Mapping[str, Any],
    *,
    unknown_arguments: UnknownArguments,
    serializer: Any,
) -> dict[str, Any]:
    """Enforce the unknown-argument policy; return ``PASSTHROUGH`` extras (else ``{}``).

    ``IGNORE`` and the "open" spec case are no-ops returning ``{}``. ``REJECT``
    raises :exc:`~rest_framework.exceptions.ValidationError` listing the
    undeclared keys. ``PASSTHROUGH`` returns the undeclared key/values so the
    caller can fold them into the dispatched callable's input. Reserved pool
    seeds are never considered "unknown".
    """
    if unknown_arguments is UnknownArguments.IGNORE:
        return {}
    declared = declared_input_keys(spec, serializer=serializer)
    if declared is None:
        return {}
    unknown = {
        key: value
        for key, value in params.items()
        if key not in declared and key not in RESERVED_POOL_SEEDS
    }
    if not unknown:
        return {}
    if unknown_arguments is UnknownArguments.REJECT:
        names = ", ".join(repr(key) for key in sorted(unknown))
        raise ValidationError({"non_field_errors": [f"Unexpected argument(s): {names}."]})
    return unknown


def service_input(serializer: Any, extras: dict[str, Any]) -> tuple[Any, Mapping[str, Any]]:
    """Return ``(data, spread_source)`` for a service pool, folding in PASSTHROUGH ``extras``.

    ``data`` is what a callable declaring ``data=`` receives; ``spread_source``
    is what the ``SPREAD_*`` binding modes spread as individual kwargs:

    - dict-validated input — ``extras`` merge into both ``data`` and the spread.
    - dataclass-validated input (opaque to the spread) — ``data`` is the
      dataclass instance unchanged; ``extras`` can reach a callable only via the
      spread (so a ``BUNDLE`` dataclass mutation drops them, by design).
    - no ``input_serializer`` — ``data`` is the ``extras`` dict, or ``None``.

    With no ``extras`` (``IGNORE`` / ``REJECT`` passed) the result is exactly the
    pre-policy input: ``data`` is ``serializer.validated_data`` and the spread is
    that same dict (services), or empty (dataclass / no serializer).
    """
    validated = serializer.validated_data if serializer is not None else None
    return service_input_for_validated(validated, extras)


def service_input_for_validated(
    validated: Any, extras: dict[str, Any]
) -> tuple[Any, Mapping[str, Any]]:
    """The ``(data, spread_source)`` fold for one already-validated value.

    Extracted from :func:`service_input` so the ``many=True`` path can reuse the
    same per-item semantics against a single element of the validated list. See
    :func:`service_input` for the dict / dataclass / no-serializer rules.
    """
    if isinstance(validated, dict):
        data = {**validated, **extras} if extras else validated
        return data, data
    if validated is not None:
        return validated, extras
    return (extras or None), extras


def guard_many_argument_binding(argument_binding: ArgumentBinding) -> None:
    """Reject a non-default ``argument_binding`` on a ``many=True`` dispatch.

    A bulk service is invoked once with the whole validated list as ``data``, so
    there is nothing to spread as individual kwargs — the ``SPREAD_*`` modes have
    no scalar client argument to act on. Failing loudly beats silently ignoring
    the request (the ``AUTO`` default resolves to ``BUNDLE`` and is a no-op here,
    so it is always allowed).
    """
    if argument_binding is not ArgumentBinding.AUTO:
        raise ValueError(
            "argument_binding is not applicable with many=True: a bulk service "
            "receives the whole list as `data`, so there are no scalar client "
            "arguments to spread. Pass argument_binding only on single-item specs."
        )


def resolve_service_many_input(
    spec: ServiceSpec[Any, Any, Any],
    serializer: Any,
    params: list[Any],
    *,
    unknown_arguments: UnknownArguments,
) -> tuple[list[Any] | None, bool]:
    """Assemble the ``data`` list for a ``many=True`` dispatch, honouring
    ``unknown_arguments`` **per list element**.

    Returns ``(data, has_data)``. Each raw item is checked against the child
    serializer's declared fields: ``REJECT`` raises on the first item carrying
    an undeclared key, ``PASSTHROUGH`` folds each item's extras into that item's
    data, ``IGNORE`` drops them (the pre-policy behaviour). ``has_data`` is
    ``False`` only for the degenerate no-serializer / no-extras case, where the
    pool omits ``data`` entirely — exactly as the single-item path omits it.

    ``argument_binding`` has no counterpart here: a bulk service receives the
    whole list as one ``data`` argument, so there are no scalar client args to
    spread. The many dispatchers reject a non-default binding upfront rather than
    silently ignore it.
    """
    child = serializer.child if serializer is not None else None
    validated = serializer.validated_data if serializer is not None else None
    data_items: list[Any] = []
    has_data = serializer is not None
    for index, raw_item in enumerate(params):
        extras = resolve_unknown_arguments(
            spec, raw_item, unknown_arguments=unknown_arguments, serializer=child
        )
        validated_item = validated[index] if validated is not None else None
        item_data, _spread = service_input_for_validated(validated_item, extras)
        data_items.append(item_data)
        if extras:
            has_data = True
    return (data_items if has_data else None), has_data


def call_target_guard(
    on_target_resolved: TargetGuard | None,
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any],
    target: Any,
    *,
    user: Any,
    request: Any,
    view: Any,
) -> None:
    """Invoke the object-permission hook with the resolved target, if supplied.

    ``target`` is the resolved row (update / RETRIEVE), the resolved set (bulk /
    LIST), or ``None`` (create / list-payload). ``dispatch_spec`` assembles the
    :class:`OfflineContext` itself — it already holds ``user`` / ``request`` /
    ``view`` — so the guard (e.g. ``enforce_permissions``) is passed by name. A
    raise aborts before the service runs. May touch the DB
    (``has_object_permission``), so the async path runs it off the event loop.
    """
    if on_target_resolved is None:
        return
    context = OfflineContext(user=user, request=request, view=view)
    on_target_resolved(spec, context, instance=target)


def resolve_provider(provider: Callable[..., Any] | None, pool: dict[str, Any]) -> dict[str, Any]:
    """Invoke a ``spec.kwargs`` / context provider through the keyword pool.

    Mirrors the framework's keyword-pool invocation convention: the provider
    receives only the subset of ``pool`` it declares. Returns ``{}`` when
    ``provider`` is ``None``.
    """
    if provider is None:
        return {}
    return dict(provider(**resolve_callable_kwargs(provider, pool)))


def shape_queryset(
    spec: SelectorSpec[Any, Any],
    qs: Any,
    *,
    view: Any,
    request: Any,
    params: Mapping[str, Any],
    source_label: str,
) -> Any:
    """Apply a selector spec's queryset shaping, ``params`` as the filter data."""
    return apply_queryset_shaping(
        qs,
        view,
        request,
        select_related=spec.select_related,
        prefetch_related=spec.prefetch_related,
        annotations=spec.annotations,
        extend_queryset=spec.extend_queryset,
        filter_set=spec.filter_set,
        filter_data=params,
        source_label=source_label,
    )


def output_serializer_for(spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any]) -> type | None:
    """The output serializer class for either spec kind (``None`` when unset)."""
    if isinstance(spec, SelectorSpec):
        return spec.output_serializer
    out = spec.output_selector_spec
    return out.output_serializer if out is not None else None


def _output_context_provider(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any],
) -> Callable[..., Any] | None:
    if isinstance(spec, SelectorSpec):
        return spec.output_serializer_context
    out = spec.output_selector_spec
    return out.output_serializer_context if out is not None else None


def resolve_output_context(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any],
    *,
    view: Any,
    request: Any,
    extras: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Resolve the output serializer context for either spec kind, or ``None``.

    ``extras`` carries the resolved-data keyword the provider may declare
    (``result`` for a mutation, ``instance`` for a retrieve, ``page`` for a
    list). Invoked through the keyword pool.
    """
    provider = _output_context_provider(spec)
    if provider is None:
        return None
    pool: dict[str, Any] = {"view": view, "request": request, **extras}
    return dict(provider(**resolve_callable_kwargs(provider, pool)))


def resolve_input_context(
    spec: ServiceSpec[Any, Any, Any],
    *,
    view: Any,
    request: Any,
) -> dict[str, Any] | None:
    """Resolve ``ServiceSpec.input_serializer_context``, or ``None``."""
    return (
        resolve_provider(spec.input_serializer_context, {"view": view, "request": request}) or None
    )


async def arun_callable(
    fn: Callable[..., Any] | Callable[..., Awaitable[Any]],
    kwargs: dict[str, Any],
) -> Any:
    """Run a selector / instance-resolver from async code, DB-safe either way.

    Async callables are awaited; sync callables run in Django's thread-sensitive
    executor so their ORM access doesn't trip ``SynchronousOnlyOperation``.
    """
    if is_async(fn):
        return await fn(**kwargs)
    return await sync_to_async(fn, thread_sensitive=True)(**kwargs)


async def arun_service_callable(
    fn: Callable[..., Any] | Callable[..., Awaitable[Any]],
    kwargs: dict[str, Any],
    *,
    atomic: bool,
) -> Any:
    """Run a service from async code (optionally atomic), DB-safe either way."""
    if is_async(fn):
        return await arun_service(fn, kwargs, atomic=atomic)
    return await sync_to_async(run_service, thread_sensitive=True)(fn, kwargs, atomic=atomic)
