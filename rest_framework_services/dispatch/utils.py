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
from typing_extensions import get_type_hints

from rest_framework_services.dispatch.base_serializer_context import base_serializer_context
from rest_framework_services.exceptions.service_validation_error import (
    ServiceValidationError,
)
from rest_framework_services.is_async import is_async
from rest_framework_services.selectors.utils import apply_queryset_shaping
from rest_framework_services.services.arun_service import arun_service
from rest_framework_services.services.run_service import run_service
from rest_framework_services.types.argument_binding import ArgumentBinding
from rest_framework_services.types.marked_input_keys import marked_input_keys
from rest_framework_services.types.offline_context import OfflineContext
from rest_framework_services.types.reserved_pool_seeds import RESERVED_POOL_SEEDS
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.types.target_guard import TargetGuard
from rest_framework_services.types.typed_dict_input import typed_dict_input
from rest_framework_services.types.unknown_arguments import UnknownArguments
from rest_framework_services.types.unpack_typed_dict import unpack_typed_dict
from rest_framework_services.types.unset import UNSET
from rest_framework_services.types.view_hooks import ViewHooks
from rest_framework_services.views.utils import resolve_callable_kwargs

# Labels handed to ``apply_queryset_shaping`` so a misconfiguration points at
# the offending spec field.
SELECTOR_SOURCE = "SelectorSpec.selector"
INSTANCE_SOURCE = "ServiceSpec.instance_selector_spec.selector"
COLLECTION_SOURCE = "ServiceSpec.collection_selector_spec.selector"
OUTPUT_SOURCE = "ServiceSpec.output_selector_spec.selector"


def strip_reserved_seeds(params: Mapping[str, Any]) -> dict[str, Any]:
    """Drop the dispatcher-owned names from a client-supplied mapping.

    :func:`merge_arguments` applies this to every spread it performs; the nested
    target resolutions (``instance_selector_spec`` / ``collection_selector_spec``)
    build their pool directly and so have to apply it themselves. Skipping it
    lets a caller-routable ``user`` / ``request`` / ``instance`` key outrank the
    dispatcher's authoritative value — see :data:`RESERVED_POOL_SEEDS`, which
    exists for exactly this reason.
    """
    return {key: value for key, value in params.items() if key not in RESERVED_POOL_SEEDS}


def view_url_kwargs(view: Any) -> dict[str, Any]:
    """Route-capture kwargs carried by the (offline) view, reserved seeds stripped.

    On HTTP a selector pool spreads ``extra_url_kwargs=view.kwargs`` (the
    ``parent_pk`` of a nested route); off-HTTP the :class:`OfflineServiceView`
    carries the same mapping (seeded by ``build_offline_context(kwargs=…)``) but
    ``dispatch_spec`` must read it explicitly. Returns ``{}`` when the view has no
    ``kwargs``. Reserved pool seeds are stripped so a route capture named after a
    seed can't clobber the dispatcher's authoritative ``request`` / ``user`` / … .
    """
    kwargs = getattr(view, "kwargs", None)
    if not kwargs:
        return {}
    return {key: value for key, value in kwargs.items() if key not in RESERVED_POOL_SEEDS}


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
    url_kwargs: Mapping[str, Any] | None = None,
) -> None:
    """Merge spread args, URL kwargs, and the ``spec.kwargs`` provider into ``pool``.

    ``binding`` must already be resolved (never ``AUTO``). ``BUNDLE`` spreads
    nothing from ``spread_source`` — only ``url_kwargs`` and the provider's keys
    join the pool. The ``SPREAD_*`` modes spread ``spread_source`` (with the
    reserved seeds stripped) and differ only in precedence against the provider:
    ``SPREAD_AUTHOR_WINS`` lets the provider override the spread,
    ``SPREAD_CALLER_WINS`` lets the spread override the provider.

    ``url_kwargs`` (a nested route's captures, e.g. ``parent_pk``) are placed
    **immediately before the provider** in every mode — so they are authoritative
    over the client spread in ``SPREAD_AUTHOR_WINS`` (the selector default: a
    route scope out-ranks client input, mirroring HTTP) while the author's
    provider remains the final say (also mirroring HTTP, where the ``kwargs``
    provider's extras apply after ``extra_url_kwargs``). Defaults to empty → the
    previous behaviour exactly.
    """
    url = url_kwargs or {}
    if binding is ArgumentBinding.BUNDLE:
        pool.update(url)
        pool.update(provider_kwargs)
        return
    spread = {k: v for k, v in spread_source.items() if k not in RESERVED_POOL_SEEDS}
    if binding is ArgumentBinding.SPREAD_AUTHOR_WINS:
        pool.update(spread)
        pool.update(url)
        pool.update(provider_kwargs)
    else:  # SPREAD_CALLER_WINS
        pool.update(url)
        pool.update(provider_kwargs)
        pool.update(spread)


def _callable_param_names(fn: Callable[..., Any]) -> set[str] | None:
    """Declared keyword-acceptable parameter names of ``fn``; ``None`` if open.

    ``None`` (open) when ``fn`` declares a bare ``**kwargs`` — it accepts
    anything, so no key can be called "unknown" (mirrors
    :func:`resolve_callable_kwargs`). A ``**kwargs: Unpack[SomeExtras]`` is *not*
    open: the ``TypedDict`` names its exact keyword surface, so those keys join
    the declared set (and only those). This is what lets ``UnknownArguments.REJECT``
    accept a URL kwarg the selector reads from its extras — instead of the old
    behaviour where the same key was accepted only because the surface happened
    to be open.

    Keys marked :data:`~rest_framework_services.NotClientInput` are **excluded**:
    they are provider-owned and never advertised, so a caller supplying one is
    supplying an argument the spec does not declare — which is what
    ``UnknownArguments.REJECT`` exists to catch. Delivery is unaffected; this
    function feeds the unknown-argument check only, never the kwargs pool.
    """
    parameters = inspect.signature(fn).parameters
    names: set[str] = set()
    for name, p in parameters.items():
        if p.kind is inspect.Parameter.VAR_KEYWORD:
            try:
                hints = get_type_hints(fn)
            except Exception:  # noqa: BLE001 — unresolvable → treat as open below
                hints = {}
            typed_dict = unpack_typed_dict(hints.get(name))
            if typed_dict is None:
                return None
            names |= set(typed_dict_input(typed_dict)[0])
        elif p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY):
            names.add(name)
    return names - marked_input_keys(fn)[1]


def _selector_consumed_keys(sel_spec: SelectorSpec[Any, Any] | None) -> set[str] | None:
    """Params keys a nested target selector consumes; ``None`` if open.

    Open when the selector has a duck-typed ``filter_set`` (its fields are
    opaque to the core) or declares a bare ``**kwargs``. A ``**kwargs:
    Unpack[TypedDict]`` is closed — its keys are enumerable (see
    :func:`_callable_param_names`).
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
    :class:`SelectorSpec` declares its ``selector``'s parameters (a
    ``**kwargs: Unpack[TypedDict]`` contributes the TypedDict's keys). ``None``
    means the set can't be enumerated (a bare ``**kwargs`` callable or a
    duck-typed ``filter_set``), so there is nothing to flag as unknown.
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


def resolve_dispatch_kwargs(fn: Callable[..., Any], pool: dict[str, Any]) -> dict[str, Any]:
    """``resolve_callable_kwargs`` plus the :data:`InputRequired` check.

    Every off-HTTP invocation of a spec's callable goes through here, so a key
    the callable marks :data:`~rest_framework_services.InputRequired` is verified
    to be **in the assembled pool** — after the caller's params, the view's URL
    kwargs, and the ``spec.kwargs`` provider have all had their say. Any of those
    channels satisfies it; the marker says the value must arrive, not where from.

    A missing key raises :exc:`ServiceValidationError`, which every transport
    already maps to a caller-visible validation failure. Without this the
    callable raises a bare ``KeyError`` from inside dispatch, which nothing maps:
    over MCP that is a 500 / JSON-RPC internal error rather than a failed tool
    result, and under an agent toolset it aborts the run instead of letting the
    model retry with the argument it omitted.

    **Off-HTTP only, deliberately.** The HTTP path assembles its pools elsewhere
    (``selectors.utils`` / ``views.mutation.utils``) and there the route *is* the
    guarantee — a capture the URLconf doesn't declare is a wiring bug, not caller
    input. The marker exists precisely because off-HTTP there is no route to
    provide that guarantee.
    """
    required, _hidden = marked_input_keys(fn)
    missing = sorted(key for key in required if key not in pool)
    if missing:
        names = ", ".join(repr(key) for key in missing)
        raise ServiceValidationError(
            {"non_field_errors": [f"Missing required argument(s): {names}."]}
        )
    return resolve_callable_kwargs(fn, pool)


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

    A key whose value is :data:`~rest_framework_services.UNSET` is **dropped** —
    the provider is *declining* to set it, not setting it to ``UNSET``. This lets
    a provider that can't resolve a value off-HTTP (middleware state absent on the
    synthetic request) step aside so a caller-supplied ``params`` value survives
    the ``SPREAD_AUTHOR_WINS`` merge, instead of a fallback ``None`` silently
    over-scoping the result. Declining is for *benign* keys only: a provider that
    owns a **scoping** key must always resolve it (off-HTTP, from ``view.kwargs``),
    since declining there would let the caller's value through — a scope bypass.
    """
    if provider is None:
        return {}
    resolved = provider(**resolve_callable_kwargs(provider, pool))
    return {key: value for key, value in resolved.items() if value is not UNSET}


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
    view_hooks: ViewHooks | None = None,
) -> dict[str, Any]:
    """Resolve the output serializer context for either spec kind.

    :func:`~rest_framework_services.dispatch.base_serializer_context.base_serializer_context`
    supplies the DRF baseline (``request`` / ``format`` / ``view``) that a
    serializer gets for free over HTTP; the spec's ``output_serializer_context``
    provider, when set, is merged **over** it and keeps the final say on every
    key. There is always a context — a spec without a provider still renders
    with the baseline, which is what keeps a serializer reading
    ``self.context["request"]`` working off HTTP.

    ``extras`` carries the resolved-data keyword the provider may declare
    (``result`` for a mutation, ``instance`` for a retrieve, ``page`` for a
    list). Invoked through the keyword pool.

    ``view_hooks`` supplies the calling view's ``get_output_serializer_context``
    / ``get_<action>_output_serializer_context`` layer, between the baseline and
    the spec provider. It is lazy — resolved with the value being rendered — so
    a provider can run one batched query against exactly that value.
    """
    context: dict[str, Any] = base_serializer_context(view=view, request=request)
    if view_hooks is not None and view_hooks.output_serializer_context is not None:
        context.update(view_hooks.output_serializer_context(extras.get("result")))
    provider = _output_context_provider(spec)
    if provider is not None:
        pool: dict[str, Any] = {"view": view, "request": request, **extras}
        context.update(provider(**resolve_callable_kwargs(provider, pool)))
    return context


def resolve_input_context(
    spec: ServiceSpec[Any, Any, Any],
    *,
    view: Any,
    request: Any,
    view_hooks: ViewHooks | None = None,
) -> dict[str, Any]:
    """Resolve the input serializer context — DRF baseline + view layers + spec.

    The input-phase twin of :func:`resolve_output_context`: an
    ``input_serializer`` validator that reads ``self.context["request"]`` (an
    ownership check, a user-scoped queryset on a ``PrimaryKeyRelatedField``)
    behaves the same whether the spec is dispatched over HTTP or off it.

    Three layers, most specific last: the baseline
    (:func:`base_serializer_context`), then the calling view's resolved
    directional / per-action hooks (:class:`ViewHooks`, absent off HTTP), then
    ``ServiceSpec.input_serializer_context``.
    """
    context: dict[str, Any] = base_serializer_context(view=view, request=request)
    if view_hooks is not None and view_hooks.input_serializer_context is not None:
        context.update(view_hooks.input_serializer_context)
    context.update(
        resolve_provider(spec.input_serializer_context, {"view": view, "request": request})
    )
    return context


def resolve_service_kwargs(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any],
    *,
    view: Any,
    request: Any,
    view_hooks: ViewHooks | None,
) -> dict[str, Any]:
    """The author-supplied kwargs for the service pool: view layers, then spec.

    ``spec.kwargs`` has the final say on overlapping keys — the documented
    precedence for the whole chain (``get_service_kwargs`` →
    ``get_<action>_service_kwargs`` → ``spec.kwargs``). The first two are already
    merged into ``view_hooks.extra_kwargs`` by the caller; resolving the spec
    provider is this core's job, and only this core's, so it runs exactly once.
    """
    kwargs: dict[str, Any] = dict((view_hooks.extra_kwargs or {}) if view_hooks else {})
    kwargs.update(resolve_provider(spec.kwargs, {"view": view, "request": request}))
    return kwargs


def resolve_input_data(
    spec: ServiceSpec[Any, Any, Any],
    *,
    view: Any,
    request: Any,
    instance: Any,
    view_hooks: ViewHooks | None,
) -> dict[str, Any]:
    """Server-provided keys merged onto the client payload before validation.

    The ``input_data`` chain (``get_input_data`` → ``get_<action>_input_data`` →
    ``ServiceSpec.input_data``), with the view layers pre-resolved into
    ``view_hooks`` and the spec provider resolved here.

    ⚠ ``spec.input_data`` used to be resolved **only** by the HTTP hook chain, so
    a spec that lifted a route capture into a serializer field validated fine over
    HTTP and failed as a missing required field over MCP. It is spec-carried
    configuration, so it belongs to the core.

    The provider pool carries ``instance`` (the resolved mutation target, ``None``
    on create) alongside ``view`` / ``request``, matching the HTTP chain — so a
    provider can shape input against the current row.
    """
    pool: dict[str, Any] = {"view": view, "request": request, "instance": instance}
    data: dict[str, Any] = dict((view_hooks.input_data or {}) if view_hooks else {})
    data.update(resolve_provider(spec.input_data, pool))
    return data


def clear_prefetch_cache(instance: Any) -> None:
    """Drop a mutated instance's stale ``_prefetched_objects_cache``.

    Mirrors DRF's ``UpdateModelMixin``: a mutating service may have changed a
    related collection the target prefetched (via a prefetching
    ``instance_selector_spec`` or the view's queryset), leaving the cache stale so
    a re-serialization reads pre-mutation related data. Guarded two ways — a no-op
    on create (``instance is None``) and when nothing was prefetched.

    Lives here rather than in the view layer because nothing about it is
    HTTP-shaped: an MCP tool call that updates a prefetched row and renders it
    back had exactly the same stale read.

    The final dispatched value is deliberately left untouched — an
    ``output_selector_spec`` re-fetch carries its own intentional
    ``prefetch_related`` that must survive.
    """
    if instance is not None and getattr(instance, "_prefetched_objects_cache", None):
        instance._prefetched_objects_cache = {}


async def arun_off_loop(fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    """Run a sync callable in Django's thread-sensitive executor and await it.

    The async dispatch path's rule for **every user-supplied sync callable it
    invokes**: a ``spec.kwargs`` provider doing a tenant lookup, an
    ``extend_queryset`` narrowing against another table, a ``filter_set`` whose
    ``filter_<name>`` method (or whose ``ModelChoiceFilter`` validation) hits the
    DB, a serializer-context provider running its batched query, a callable
    ``success_status`` reading a relation off the result. None of them are
    declared async — a spec is written once and dispatched over both transports —
    so calling them from the event loop raises ``SynchronousOnlyOperation`` the
    moment they touch the ORM, and the failure surfaces only under the async
    transport, only for the specs that happen to query.

    ``thread_sensitive=True`` keeps them in the same executor as the selector /
    service / permission calls around them, so they share one connection and see
    the same transaction state as they would on the sync path.

    Not for the spec's own selector / service: those may legitimately be ``async
    def``, so they go through :func:`arun_callable` / :func:`arun_service_callable`,
    which await a coroutine and offload only what is sync.
    """
    return await sync_to_async(fn, thread_sensitive=True)(*args, **kwargs)


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
