"""Internal helpers shared by the transport-neutral dispatch + render surface."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from asgiref.sync import sync_to_async
from rest_framework.exceptions import ValidationError
from typing_extensions import get_type_hints

from rest_framework_services.dispatch.base_serializer_context import base_serializer_context
from rest_framework_services.dispatch.combine_progress import combine_progress
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

# Handed to ``apply_queryset_shaping`` so a misconfiguration names the offending
# spec field.
SELECTOR_SOURCE = "SelectorSpec.selector"
INSTANCE_SOURCE = "ServiceSpec.instance_selector_spec.selector"
COLLECTION_SOURCE = "ServiceSpec.collection_selector_spec.selector"
OUTPUT_SOURCE = "ServiceSpec.output_selector_spec.selector"


def strip_reserved_seeds(params: Mapping[str, Any]) -> dict[str, Any]:
    """Drop the dispatcher-owned names from a client-supplied mapping.

    :func:`merge_arguments` applies this to every spread it performs, but the
    nested target resolutions build their pool directly and must call it
    themselves — skipping it lets a caller-supplied ``user`` / ``request`` /
    ``instance`` outrank the dispatcher's authoritative value.
    """
    return {key: value for key, value in params.items() if key not in RESERVED_POOL_SEEDS}


def view_url_kwargs(view: Any) -> dict[str, Any]:
    """Route-capture kwargs carried by the (offline) view, reserved seeds stripped.

    On HTTP the selector pool picks these up as ``extra_url_kwargs=view.kwargs``;
    off-HTTP the :class:`OfflineServiceView` carries the same mapping but
    ``dispatch_spec`` has to read it explicitly. Stripping the reserved seeds
    stops a capture named after one clobbering the dispatcher's own value.
    """
    kwargs = getattr(view, "kwargs", None)
    if not kwargs:
        return {}
    return {key: value for key, value in kwargs.items() if key not in RESERVED_POOL_SEEDS}


def resolve_argument_binding(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any],
    argument_binding: ArgumentBinding,
) -> ArgumentBinding:
    """Resolve ``AUTO`` to the per-spec-type default; pass any other mode through."""
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

    ``binding`` must already be resolved — never ``AUTO``.

    ``url_kwargs`` (a nested route's captures) go **immediately before the
    provider** in every mode, so a route scope out-ranks client input while the
    author's provider keeps the final say. Both mirror the HTTP ordering, where
    the ``kwargs`` provider's extras apply after ``extra_url_kwargs``.
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

    A bare ``**kwargs`` is open — it accepts anything, so no key can be called
    "unknown". A ``**kwargs: Unpack[SomeExtras]`` is *not*: the ``TypedDict``
    names an exact keyword surface, and only those keys join the declared set.

    Keys marked :data:`~rest_framework_services.NotClientInput` are excluded as
    provider-owned and never advertised. Delivery is unaffected — this feeds the
    unknown-argument check only, never the kwargs pool.
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

    A duck-typed ``filter_set`` is open: its fields are opaque to the core.
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

    Derived from the spec alone — no transport knowledge. A ``ServiceSpec``
    declares its ``input_serializer`` fields plus whatever its nested target
    selectors consume (e.g. the ``pk`` an ``instance_selector_spec`` reads).
    ``None`` means the set is not enumerable, so nothing can be flagged unknown.
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

    ``PASSTHROUGH`` returns the undeclared key/values so the caller can fold them
    into the dispatched callable's input. Reserved pool seeds are never
    considered unknown.
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

    Must run against the **fully assembled** pool: any channel (caller params,
    URL kwargs, the ``spec.kwargs`` provider) satisfies the marker, which says
    the value must arrive, not where from.

    Raises :exc:`ServiceValidationError` rather than letting the callable raise
    ``KeyError``, because every transport maps the former to a caller-visible
    validation failure and none maps the latter.

    Off-HTTP only, deliberately: the HTTP path assembles its pools in
    ``selectors.utils`` / ``views.mutation.utils``, where the route is the
    guarantee.
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
      spread, so a ``BUNDLE`` dataclass mutation drops them, by design.
    - no ``input_serializer`` — ``data`` is the ``extras`` dict, or ``None``.
    """
    validated = serializer.validated_data if serializer is not None else None
    return service_input_for_validated(validated, extras)


def service_input_for_validated(
    validated: Any, extras: dict[str, Any]
) -> tuple[Any, Mapping[str, Any]]:
    """The ``(data, spread_source)`` fold for one already-validated value.

    Split out of :func:`service_input` so the ``many=True`` path gets identical
    per-item semantics; see there for the dict / dataclass / no-serializer rules.
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
    the ``SPREAD_*`` modes have no scalar client argument to act on. ``AUTO``
    resolves to ``BUNDLE`` and is a no-op here, so it is always allowed.
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

    Each raw item is checked against the *child* serializer's fields, so
    ``REJECT`` raises on the first offending item. ``has_data`` is ``False`` only
    for the degenerate no-serializer / no-extras case, where the pool must omit
    ``data`` entirely — exactly as the single-item path omits it.
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
    LIST), or ``None`` (create / list-payload). A raise aborts before the service
    runs. May touch the DB (``has_object_permission``), so the async path must
    run it off the event loop.
    """
    if on_target_resolved is None:
        return
    context = OfflineContext(user=user, request=request, view=view)
    on_target_resolved(spec, context, instance=target)


def call_preconditions(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any],
    pool: dict[str, Any],
) -> None:
    """Run a spec's ``preconditions`` through the keyword pool, in order.

    Callers must invoke this **after** validation and target resolution and
    **before** the service: permissions → target resolution → validation →
    preconditions → service. That ordering is what lets a state rule over the
    resolved row and a coherence rule over the validated payload share one pool,
    and it keeps business logic off an unvalidated payload. Raise-to-abort — the
    return value is ignored, so a predicate written ``-> bool`` returning
    ``False`` is silently a no-op.
    """
    for precondition in spec.preconditions or ():
        precondition(**resolve_dispatch_kwargs(precondition, pool))


async def acall_preconditions(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any],
    pool: dict[str, Any],
) -> None:
    """:func:`call_preconditions` for the async path.

    Preconditions are sync-only, like every auxiliary callable a spec carries:
    only ``selector`` and ``service`` may be ``async def``, because a spec is
    written once for both transports and an ``async def`` precondition would be
    un-callable on the sync path. Assumed to query, hence the executor.
    """
    for precondition in spec.preconditions or ():
        await arun_off_loop(precondition, **resolve_dispatch_kwargs(precondition, pool))


def resolve_progress(
    spec: Any,
    progress: Any,
    *,
    user: Any,
    request: Any,
    view: Any,
    view_hooks: Any = None,
) -> Any:
    """Fan every progress sink this dispatch has into one reporter.

    The transport-native sink (the caller's ``progress``, the view's hook) and
    the transport-independent one the spec declares must both fire, so
    ``combine_progress`` merges them and isolates each — one failing sink neither
    silences the other nor escapes into the service. A ``progress_reporter``
    returning ``None`` is *declining*, and leaves the transport's reporter in
    place rather than replacing it with a no-op.

    The pool is deliberately small: the full pool is not built yet — this is one
    of its seeds — so the provider cannot see validated ``data`` or the resolved
    ``instance``.
    """
    # A view is just another transport from the core's point of view, so its hook
    # merges on the same footing as the caller's ``progress``.
    transport = combine_progress(progress, getattr(view_hooks, "progress", None))
    provider = getattr(spec, "progress_reporter", None)
    if provider is None:
        return transport
    resolved = provider(
        **resolve_callable_kwargs(provider, {"user": user, "request": request, "view": view})
    )
    return combine_progress(transport, resolved)


def resolve_provider(provider: Callable[..., Any] | None, pool: dict[str, Any]) -> dict[str, Any]:
    """Invoke a ``spec.kwargs`` / context provider through the keyword pool.

    A key whose value is :data:`~rest_framework_services.UNSET` is dropped: the
    provider is *declining* to set it, not setting it to ``UNSET``. That lets a
    provider unable to resolve a value off-HTTP step aside so a caller-supplied
    ``params`` value survives the merge, instead of a fallback ``None`` silently
    over-scoping the result. Declining is for benign keys only — a provider
    owning a *scoping* key must always resolve it, since declining would let the
    caller's value through as a scope bypass.
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

    Three layers, most specific last: the DRF baseline (``request`` / ``format``
    / ``view``), the calling view's hooks, then the spec's provider. There is
    always a context — the baseline alone is what keeps a serializer reading
    ``self.context["request"]`` working off HTTP.

    ``extras`` carries the resolved-data keyword the provider may declare
    (``result`` / ``instance`` / ``page``). The ``view_hooks`` layer is lazy,
    resolved with the value being rendered, so a provider can batch one query
    against exactly that value.
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

    The input-phase twin of :func:`resolve_output_context`, and same layering
    order: an ``input_serializer`` validator reading ``self.context["request"]``
    behaves the same over HTTP and off it.
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

    Chain precedence is ``get_service_kwargs`` → ``get_<action>_service_kwargs``
    → ``spec.kwargs``; callers pre-merge the first two into
    ``view_hooks.extra_kwargs``. Resolving ``spec.kwargs`` is this core's job and
    only this core's, so the provider runs exactly once per dispatch.
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

    The provider pool carries ``instance`` (the resolved mutation target, ``None``
    on create) alongside ``view`` / ``request``, matching the HTTP chain, so a
    provider can shape input against the current row.
    """
    pool: dict[str, Any] = {"view": view, "request": request, "instance": instance}
    data: dict[str, Any] = dict((view_hooks.input_data or {}) if view_hooks else {})
    data.update(resolve_provider(spec.input_data, pool))
    return data


def clear_prefetch_cache(instance: Any) -> None:
    """Drop a mutated instance's stale ``_prefetched_objects_cache``.

    Mirrors DRF's ``UpdateModelMixin``: a mutating service may have changed a
    related collection the target prefetched, leaving the cache stale so a
    re-serialization reads pre-mutation related data.

    Only the mutation target is cleared. The final dispatched value must be left
    untouched — an ``output_selector_spec`` re-fetch carries its own intentional
    ``prefetch_related`` that has to survive.
    """
    if instance is not None and getattr(instance, "_prefetched_objects_cache", None):
        instance._prefetched_objects_cache = {}


async def arun_off_loop(fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    """Run a sync callable in Django's thread-sensitive executor and await it.

    The async dispatch path must route **every user-supplied sync callable**
    through here — ``kwargs`` providers, ``extend_queryset``, ``filter_set``,
    serializer-context providers, a callable ``success_status``. None of them may
    be async, so calling one from the event loop raises
    ``SynchronousOnlyOperation`` the moment it touches the ORM — a failure that
    surfaces only under the async transport, only for specs that happen to query.
    ``thread_sensitive=True`` puts them in the same executor as the surrounding
    selector / service / permission calls, so they share one connection and see
    the same transaction state as on the sync path.

    Not for the spec's own selector / service, which may be ``async def``; those
    go through :func:`arun_callable` / :func:`arun_service_callable`.
    """
    return await sync_to_async(fn, thread_sensitive=True)(*args, **kwargs)


async def arun_callable(
    fn: Callable[..., Any] | Callable[..., Awaitable[Any]],
    kwargs: dict[str, Any],
) -> Any:
    """Run a selector / instance-resolver from async code, DB-safe either way.

    Sync callables go to the thread-sensitive executor so their ORM access
    doesn't trip ``SynchronousOnlyOperation``.
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


# --- naming a refusal what the request called it -------------------------
#
# A service raises about the model: ``{"title": [...]}``, because the model is
# what it was handed. The request may have said something else -- a serializer
# field declares ``source=`` precisely to let the two diverge -- and by the time
# the service runs, that name is gone. DRF resolves ``source=`` while building
# ``validated_data``, at every depth, so the wire name reaches neither the
# service nor the mutation helpers nor a relation spec. The serializer is the
# one thing still holding both vocabularies, and it holds them for free.


def _wire_names(serializer: Any) -> dict[str, tuple[str, Any]]:
    """``{source: (wire_name, nested)}`` for one serializer's writable fields.

    ``nested`` is the same mapping for a field that is itself a serializer, so
    the result describes the whole input tree rather than its top level.
    ``many=True`` is that same tree one indirection away, on ``child``.

    Read-only fields are skipped: their ``source`` cannot appear in an error
    about input, and including them would let one shadow the writable field
    that can. Two writable fields sharing a ``source`` is not a shape DRF can
    save, so the first is taken rather than guessed between. A dotted
    ``source="author.name"`` and ``source="*"`` are skipped -- neither is a key
    of ``validated_data``, so neither can be a key of an error about it.
    """
    child: Any = getattr(serializer, "child", None)
    fields: Any = getattr(child if child is not None else serializer, "fields", None)
    if fields is None:
        return {}
    names: dict[str, tuple[str, Any]] = {}
    for wire_name, field in fields.items():
        source: str = field.source
        if field.read_only or "." in source or source == "*" or source in names:
            continue
        names[source] = (wire_name, _wire_names(field) or None)
    return names


def _wire_named_detail(detail: Any, names: dict[str, tuple[str, Any]]) -> Any:
    """``detail`` with every key the serializer knows a wire name for renamed.

    A key with no entry passes through untouched, which is what keeps
    ``non_field_errors`` and anything else a service invented intact -- the
    walk renames what it can name and never guesses. A list is walked without
    descending a level, because that is the shape a collection's error already
    has: one entry per incoming row, each keyed like the row.
    """
    if isinstance(detail, dict):
        renamed: dict[str, Any] = {}
        for key, value in detail.items():
            wire_name, nested = names.get(key, (key, None))
            renamed[wire_name] = _wire_named_detail(value, nested) if nested else value
        return renamed
    if isinstance(detail, list):
        return [_wire_named_detail(item, names) for item in detail]
    return detail


def wire_named_error(
    exc: ServiceValidationError | ValidationError,
    serializer: Any,
) -> ServiceValidationError | ValidationError:
    """The same refusal, keyed by the names the request actually used.

    The class is preserved for the reason the row writers preserve it: a
    service that reached for DRF's error chose its status mapping with it.
    """
    detail: Any = _wire_named_detail(exc.detail, _wire_names(serializer))
    if isinstance(exc, ServiceValidationError):
        return ServiceValidationError(detail)
    return ValidationError(detail)


@contextmanager
def wire_named_errors(serializer: Any) -> Iterator[None]:
    """Rename the keys of any validation error raised inside the block.

    Wraps the preconditions and the service call together: both speak about the
    input, so both owe the caller names the caller can act on. Without an
    ``input_serializer`` there is no second vocabulary and the block is a
    pass-through.
    """
    if serializer is None:
        yield
        return
    try:
        yield
    except (ServiceValidationError, ValidationError) as exc:
        raise wire_named_error(exc, serializer) from exc
