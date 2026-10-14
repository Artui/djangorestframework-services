"""Internal helpers shared by the transport-neutral dispatch + render surface."""

from __future__ import annotations

import dataclasses
import inspect
import threading
import weakref
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any, cast

from asgiref.sync import sync_to_async
from django.core.exceptions import ImproperlyConfigured
from django.http import QueryDict
from django_service_specs.adapters.filters.filter_set_parameters import filter_set_parameters
from django_service_specs.dispatch.grant import Grant
from django_service_specs.dispatch.resolve_target import resolve_target
from django_service_specs.parameters.coerce_flat import coerce_flat
from django_service_specs.parameters.parameters import Parameters
from django_service_specs.specs.selector_spec import SelectorSpec as KernelSelectorSpec
from django_service_specs.specs.service_spec import ServiceSpec as KernelServiceSpec
from rest_framework.exceptions import ValidationError
from rest_framework.settings import api_settings

from rest_framework_services.dispatch.base_serializer_context import base_serializer_context
from rest_framework_services.dispatch.combine_progress import combine_progress
from rest_framework_services.exceptions.service_validation_error import (
    ServiceValidationError,
)
from rest_framework_services.service_specs.invalid_arguments import InvalidArguments
from rest_framework_services.service_specs.kernel_spec_variant import kernel_spec_variant
from rest_framework_services.service_specs.translate_dispatch_errors import (
    translate_dispatch_errors,
)
from rest_framework_services.service_specs.utils import SERIALIZER_CONTEXT_SEED, describe_serializer
from rest_framework_services.types.argument_binding import ArgumentBinding
from rest_framework_services.types.dispatch_result import DispatchResult
from rest_framework_services.types.marked_input_keys import marked_input_keys
from rest_framework_services.types.offline_context import OfflineContext
from rest_framework_services.types.pool_seeds import PoolSeeds
from rest_framework_services.types.progress_reporter import ProgressReporter
from rest_framework_services.types.query_param import QueryParam
from rest_framework_services.types.reserved_pool_seeds import RESERVED_POOL_SEEDS
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.types.target_guard import TargetGuard
from rest_framework_services.types.typed_dict_input import typed_dict_input
from rest_framework_services.types.unknown_arguments import UnknownArguments
from rest_framework_services.types.unpack_typed_dict import unpack_typed_dict
from rest_framework_services.types.unset import UNSET
from rest_framework_services.types.url_kwarg import UrlKwarg
from rest_framework_services.types.utils import (
    resolved_annotation,
)
from rest_framework_services.types.view_hooks import ViewHooks
from rest_framework_services.views.utils import resolve_callable_kwargs

# ---------------------------------------------------------------------------
# The DRF edge over the kernel's ``dispatch``.
#
# ``dispatch_spec`` and ``adispatch_spec`` differ only in the one call they make
# to the kernel, so everything before it is ``prepare_edge_call`` (sync: it may
# resolve the target and run the guard, both of which query) and everything
# around it is ``edge_errors``. The async half hops once around the first and
# awaits ``adispatch`` with what it returns.
# ---------------------------------------------------------------------------

# The pool seed the guard of a selector spec rides in on. A selector spec takes no
# ``target=``, so the guard runs as a precondition the edge prepends, and a
# compiled spec is shared by every call, so the per-call guard reaches that
# precondition through the call's own seeds rather than through the spec.
TARGET_GUARD_SEED = "_target_guard"
# The names the edge registers as seeds on every call. ``serializer`` is not one:
# the Validator's own pool delivers it, and the kernel refuses a Validator that
# binds a name a seed occupies. The view's serializer context layers ride in the
# third, read by the adapters beneath the spec's own context callables.
EDGE_SEEDS: tuple[str, ...] = ("request", "view", SERIALIZER_CONTEXT_SEED)


@dataclasses.dataclass(frozen=True)
class EdgeCall:
    """One call of the DRF edge, prepared: ``dispatch(spec, **kwargs)`` runs it.

    ``spec`` is the compiled variant the ``Grant`` in ``kwargs`` was minted for;
    ``kwargs`` is everything else ``dispatch`` and ``adispatch`` take, by name.
    """

    spec: KernelServiceSpec | KernelSelectorSpec
    kwargs: dict[str, Any]


def prepare_edge_call(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any],
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
    instance: Any,
    filter_data: Mapping[str, Any] | None,
    many_as_argument: bool,
    partial: bool | None,
) -> EdgeCall | DispatchResult:
    """Everything ``dispatch_spec`` does before the kernel's ``dispatch``, as one sync step.

    Returns the prepared call, or the kernel's not-found ``DispatchResult`` when
    a service spec's target lookup found no row. Runs the guard, so it is the
    step an async caller hops to a thread for, once. Raise the kernel's own
    errors; run it inside ``edge_errors``.
    """
    refuse_retired_modes(argument_binding, unknown_arguments)
    if not isinstance(spec, ServiceSpec | SelectorSpec):
        raise TypeError(
            f"dispatch_spec expects a ServiceSpec or SelectorSpec; got {type(spec).__name__}."
        )
    hooks = view_hooks if view_hooks is not None else ViewHooks()
    # One object for the whole call: a ``Grant`` binds its principal by identity.
    principal = user if user is not None else unauthenticated_user()
    seeds = edge_seeds(pool_seeds, request=request, view=view, view_hooks=hooks)
    shared: dict[str, Any] = {
        "principal": principal,
        "unknown_arguments": unknown_arguments,
        "progress": (
            progress if hooks.progress is None else combine_progress(progress, hooks.progress)
        ),
    }
    guard_context = OfflineContext(user=user, request=request, view=view)
    if isinstance(spec, SelectorSpec):
        variant: Any = kernel_spec_variant(spec, unguarded=True)
        if on_target_resolved is not None:
            variant = guarded_selector(variant)
            guard = on_target_resolved

            def bound_guard(target: Any) -> None:
                guard(spec, guard_context, instance=target)

            seeds = seeds.extend(**{TARGET_GUARD_SEED: lambda: bound_guard})
        arguments = edge_arguments(variant, spec, params, request=request, view=view)
        _route_filter_data(arguments, variant.filter_set, filter_data)
        return EdgeCall(
            variant,
            {
                **shared,
                "arguments": arguments,
                "extra_kwargs": _route_hook_extras(variant, arguments, hooks.extra_kwargs),
                "grant": Grant(variant, principal),
                "pool_seeds": seeds,
            },
        )
    if spec.many:
        variant = kernel_spec_variant(spec, partial=partial, unguarded=True)
        rows = params
        if many_as_argument:
            arguments = edge_arguments(variant, spec, params, request=request, view=view)
        else:
            arguments = edge_arguments(variant, spec, {}, request=request, view=view)
            arguments[spec.many_argument] = rows
        if hooks.input_data and isinstance(arguments.get(spec.many_argument), list):
            arguments[spec.many_argument] = [
                {**row, **hooks.input_data} if isinstance(row, Mapping) else row
                for row in arguments[spec.many_argument]
            ]
        extra_kwargs = _route_hook_extras(variant, arguments, hooks.extra_kwargs)
        # A bulk call acts on no target, as on main: the guard is asked about ``None``.
        if on_target_resolved is not None:
            on_target_resolved(spec, guard_context, instance=None)
        return EdgeCall(
            variant,
            {
                **shared,
                "arguments": arguments,
                "extra_kwargs": extra_kwargs,
                "grant": Grant(variant, principal, target_checked=True),
                "pool_seeds": seeds,
            },
        )
    # One arc to coverage, so each conjunct is named by the test that fails
    # without it: test_a_create_is_guarded_with_none (an unset ``instance`` is
    # no row) and test_instance_none_on_a_spec_with_no_lookup_is_a_create (a
    # ``None`` one is no row either, and takes no stand-in lookup).
    handed = instance is not UNSET and instance is not None
    # A drfs ``ServiceSpec`` compiles to the kernel's.
    service = cast(
        KernelServiceSpec,
        kernel_spec_variant(spec, partial=partial, unguarded=True, target=handed),
    )
    arguments = edge_arguments(service, spec, params, request=request, view=view)
    target_spec = service.target_selector_spec()
    if target_spec is not None:
        _route_filter_data(arguments, target_spec.filter_set, filter_data)
    if hooks.input_data and isinstance(arguments, dict):
        # Beneath ``spec.input_data``, which the kernel merges over the arguments.
        arguments.update(hooks.input_data)
    # Before the lookup, which a hook value for one of its reads scopes.
    extra_kwargs = _route_hook_extras(service, arguments, hooks.extra_kwargs)
    if handed:
        target = instance
    elif instance is None:
        # The caller says there is no row, so no lookup runs. On a spec with its
        # own lookup the kernel answers that ``None`` as ``not_found``
        # (test_instance_none_on_a_spec_with_a_lookup_is_not_found). A create
        # with no ``instance`` falls through to ``resolve_target``, which finds
        # it no target and asks nothing.
        target = None
    else:
        resolved = resolve_target(
            service,
            principal=principal,
            arguments=arguments,
            grant=Grant(service, principal),
            pool_seeds=seeds,
        )
        if isinstance(resolved, DispatchResult):
            return resolved
        target = resolved[0]
    if on_target_resolved is not None:
        on_target_resolved(spec, guard_context, instance=target)
    return EdgeCall(
        service,
        {
            **shared,
            "arguments": arguments,
            "grant": Grant(service, principal, target_checked=True),
            "pool_seeds": seeds,
            "target": target,
            "extra_kwargs": extra_kwargs,
        },
    )


def refuse_retired_modes(
    argument_binding: ArgumentBinding, unknown_arguments: UnknownArguments
) -> None:
    """Refuse the two policies the kernel's one pool retired; their members stay importable."""
    if argument_binding is ArgumentBinding.SPREAD_CALLER_WINS:
        raise ValueError(
            "ArgumentBinding.SPREAD_CALLER_WINS is retired: a call has one pool, where the "
            "caller's argument for a declared parameter is what the callable receives unless "
            "the spec's kwargs provider is annotated to fill it. Pass ArgumentBinding.AUTO, "
            "and leave the key out of the kwargs provider where the caller's value should win."
        )
    if unknown_arguments is UnknownArguments.PASSTHROUGH:
        raise ValueError(
            "UnknownArguments.PASSTHROUGH is retired: an argument the spec does not declare "
            "never reaches a callable. Declare it (a reads entry, a serializer field or a "
            "parameter), then pass UnknownArguments.IGNORE or UnknownArguments.REJECT."
        )


def unauthenticated_user() -> Any:
    """DRF's unauthenticated user, as a request with no credentials carries it.

    Read from ``api_settings`` at call time, which imports the class lazily:
    ``django.contrib.auth.models`` cannot be imported while the app registry is
    still loading, and this package is imported then. ``None`` where a project
    sets ``UNAUTHENTICATED_USER`` to ``None``, as DRF's own ``Request`` does.
    """
    # A class, or ``None``; DRF's settings object is untyped.
    factory: Any = api_settings.UNAUTHENTICATED_USER
    return factory() if factory is not None else None


def edge_seeds(
    pool_seeds: PoolSeeds, *, request: Any, view: Any, view_hooks: ViewHooks
) -> PoolSeeds:
    """The caller's seeds, behind this call's ``request``, ``view`` and view hooks.

    The kernel resolves each seed against the base pool alone, so a resolver of
    the caller's that declares ``request`` or ``view`` - as this package's seeds
    always could - is handed them by a wrapper, which stays sync.

    ``view_hooks`` is registered under ``SERIALIZER_CONTEXT_SEED``, where the
    adapters read the view's serializer context layers from, **only when it
    carries one**: a seed is in every ``**kwargs`` callable's pool, so a call
    with no context hooks, as every transport but the HTTP views makes, adds
    nothing there (test_a_call_with_no_context_hooks_registers_no_seed). One
    branch to coverage, so each condition is named by the test that fails
    without it: test_an_input_half_alone_reaches_the_input_serializer and
    test_an_output_half_alone_reaches_the_output_serializer.

    Raises:
        ImproperlyConfigured: ``pool_seeds`` registers ``request``, ``view`` or
            ``SERIALIZER_CONTEXT_SEED``, which this edge seeds itself from what
            it was handed.
    """
    clash = sorted(pool_seeds.names & set(EDGE_SEEDS))
    if clash:
        raise ImproperlyConfigured(
            f"pool_seeds registers {', '.join(repr(name) for name in clash)}, which "
            "dispatch_spec seeds itself from the request, view and view_hooks it is handed. "
            "Pass them as request=, view= and view_hooks=, and remove the registration."
        )
    ambient = {"request": request, "view": view}
    wrapped = tuple((name, _with_ambient(resolver, ambient)) for name, resolver in pool_seeds.seeds)
    layers: tuple[tuple[str, Callable[..., Any]], ...] = ()
    if (
        view_hooks.input_serializer_context is not None
        or view_hooks.output_serializer_context is not None
    ):
        layers = ((SERIALIZER_CONTEXT_SEED, lambda: view_hooks),)
    return PoolSeeds(
        seeds=(("request", lambda: request), ("view", lambda: view), *layers, *wrapped)
    )


def _with_ambient(resolver: Callable[..., Any], ambient: Mapping[str, Any]) -> Callable[..., Any]:
    def resolve(**pool: Any) -> Any:
        return resolver(**resolve_callable_kwargs(resolver, {**pool, **ambient}))

    return resolve


def edge_arguments(
    variant: KernelServiceSpec | KernelSelectorSpec,
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any],
    params: Any,
    *,
    request: Any,
    view: Any,
) -> Any:
    """``params`` as the kernel's ``arguments``, with the declared channels routed both ways.

    A ``QueryDict`` is read flat - a repeated key as a list where the parameter
    is an array - and coerced as a query string is. Then the names a route
    captures: each declared ``UrlKwarg``, and what the selector or the target
    lookup reads. A value on ``view.kwargs`` is the argument, authoritative over
    ``params`` as a route capture always was, coerced from the string a URLconf
    may capture; a declared ``UrlKwarg`` the caller sent and the view does not
    carry goes onto ``view.kwargs``, so a provider reading it there finds it.
    Each declared ``QueryParam`` the same way with ``request.query_params``.
    Arguments that are no mapping are handed on as they are, for the kernel's
    shape check to refuse.
    """
    parameters = variant.parameters()
    if isinstance(params, QueryDict):
        arguments: dict[str, Any] = coerce_flat(parameters, _flat(parameters, params))
    elif isinstance(params, Mapping):
        arguments = dict(params)
    else:
        return params
    declared = tuple(spec.reads or ())
    url_kwargs = {entry.name for entry in declared if isinstance(entry, UrlKwarg)}
    reader = variant.target_selector_spec() if isinstance(variant, KernelServiceSpec) else variant
    routed = url_kwargs | (reader.reads.names() if reader is not None else frozenset())
    route = getattr(view, "kwargs", None)
    if isinstance(route, dict):
        captured = {name: route[name] for name in routed if name in route}
        arguments.update(coerce_flat(parameters, captured))
        for name in sorted(url_kwargs - route.keys()):
            if name in arguments:
                route[name] = arguments[name]
    queried = [entry.name for entry in declared if isinstance(entry, QueryParam)]
    query = getattr(request, "query_params", None)
    if queried and isinstance(query, QueryDict):
        sent = {name: arguments[name] for name in queried if name in arguments}
        absent = [name for name in queried if name not in arguments and name in query]
        arguments.update(coerce_flat(parameters, _flat(parameters, query, names=absent)))
        if sent.keys() - query.keys():
            # The request is the call's own (an offline one is built per call, an
            # HTTP one per request), so its query string is replaced, never shared.
            merged = query.copy()
            for name, value in sent.items():
                if name not in query:
                    merged.setlist(name, value if isinstance(value, list) else [value])
            merged._mutable = False
            request._request.GET = merged
    return arguments


def _flat(
    parameters: Parameters, source: QueryDict, *, names: Iterable[str] | None = None
) -> dict[str, Any]:
    """A ``QueryDict``'s values, a list where the parameter is an array and the last value else."""
    wanted = source.keys() if names is None else names
    flat: dict[str, Any] = {}
    for name in wanted:
        declared = parameters.get(name)
        flat[name] = (
            source.getlist(name)
            if declared is not None and declared.type == "array"
            else source.get(name)
        )
    return flat


def _route_hook_extras(
    variant: KernelServiceSpec | KernelSelectorSpec,
    arguments: Any,
    extras: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    """Split a view hook's extras: a declared read's value into ``arguments``, the rest returned.

    A view's ``get_*_kwargs`` hooks have always supplied values for the names a
    spec reads - ``get_selector_kwargs()`` returning the selector's ``tenant``
    - and the kernel refuses ``extra_kwargs`` naming a declared parameter. So a
    hook value for a read of the compiled spec (its service's or selector's, or
    a service spec's target lookup's) goes into the arguments, over the
    client's, as a view layer always outranked the request; what is left is
    returned for ``dispatch(extra_kwargs=)``. Arguments that are no mapping
    take nothing, for the kernel's shape check to refuse.

    The spec's own provider still wins: the kernel drops the caller's value for
    a name its typed ``kwargs`` provider fills, wherever that value came from
    (test_the_specs_typed_provider_wins_over_a_hook_value). One arc to coverage,
    so each condition that keeps a value an extra is named by the test that
    fails without it: an ``UNSET`` value supplies nothing
    (test_an_unset_hook_value_leaves_the_clients), and a name that is no read
    stays the overlay's (test_a_hook_value_for_a_hidden_name_still_goes_as_an_extra).
    """
    if not extras:
        return extras
    reads = set(variant.reads.names())
    if isinstance(variant, KernelServiceSpec):
        target_spec = variant.target_selector_spec()
        if target_spec is not None:
            reads |= target_spec.reads.names()
    routed = {name: value for name, value in extras.items() if value is not UNSET and name in reads}
    if isinstance(arguments, dict):
        arguments.update(routed)
    return {name: value for name, value in extras.items() if name not in routed}


def _route_filter_data(
    arguments: Any, filter_set: Any, filter_data: Mapping[str, Any] | None
) -> None:
    """Put ``filter_data`` into ``arguments``, restricted to the names ``filter_set`` reads.

    With no ``filter_data`` the arguments carry the filter's values themselves,
    as one flat mapping off HTTP usually does. With it, a filter name is read
    from it alone, as ``filter_data`` always replaced ``params`` as the filter's
    source; a ``QueryDict`` is read flat and coerced, a repeated key as a list.
    """
    # One arc to coverage, so each operand is named:
    # test_filter_data_reaches_the_filter_set_and_nothing_else fails without the
    # second, and test_no_filter_data_leaves_the_arguments_to_filter without the first.
    if filter_set is None or filter_data is None or not isinstance(arguments, dict):
        return
    parameters = filter_set_parameters(filter_set)
    names = parameters.names()
    for name in names:
        arguments.pop(name, None)
    if isinstance(filter_data, QueryDict):
        present = [name for name in names if name in filter_data]
        arguments.update(coerce_flat(parameters, _flat(parameters, filter_data, names=present)))
        return
    arguments.update({name: filter_data[name] for name in names if name in filter_data})


# id(unguarded variant) -> (a weak reference to it, its guarded twin). By identity,
# as ``kernel_spec_variant`` keys its own: a ``Grant`` binds the compiled object.
_GUARDED: dict[int, tuple[weakref.ref[Any], KernelSelectorSpec]] = {}
_GUARDED_LOCK = threading.Lock()


def guarded_selector(variant: KernelSelectorSpec) -> KernelSelectorSpec:
    """``variant`` with the call's target guard as its first precondition, compiled once.

    The kernel runs a selector spec's preconditions on what it found - the row
    as ``instance`` for a retrieve, the set as ``collection`` for a list - and
    never on a missing row or a ``None`` its ``allow_none`` let through. So the
    guard runs where it always did: after the lookup, before the author's own
    preconditions, and not at all where there is no row. The guard itself is the
    call's, read from the ``TARGET_GUARD_SEED`` seed.
    """
    with _GUARDED_LOCK:
        held = _GUARDED.get(id(variant))
        if held is None:
            source_id = id(variant)
            guard = _guard_row if variant.kind is SelectorKind.RETRIEVE else _guard_set
            twin = dataclasses.replace(
                variant, preconditions=(guard, *(variant.preconditions or ()))
            )
            source = weakref.ref(variant, lambda _dead: _GUARDED.pop(source_id, None))
            held = _GUARDED[source_id] = (source, twin)
    return held[1]


def _guard_row(*, instance: Any, _target_guard: Callable[[Any], None]) -> None:
    _target_guard(instance)


def _guard_set(*, collection: Any, _target_guard: Callable[[Any], None]) -> None:
    _target_guard(collection)


@contextmanager
def edge_errors(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any],
    params: Any,
    *,
    many_as_argument: bool,
) -> Iterator[None]:
    """The kernel's refusals as DRF's, around a whole edge call.

    - Every kernel ``DispatchError`` leaves as its dual-base twin, through
      ``translate_dispatch_errors``.
    - A ``many`` spec whose rows arrived as ``params`` itself - an HTTP list
      body - is refused as DRF's list serializer refuses one: an entry per row,
      ``{}`` for a valid one, and a refusal of the list itself under
      ``non_field_errors``. The kernel keys both under ``many_argument``, which
      such a caller never sent.
    - A validation error a service or a precondition raises is keyed by the
      names the request used, where the input serializer's ``source=`` makes
      them differ from the ones the service saw. Never the kernel's own
      ``InvalidArguments``, which is already keyed by them.
    """
    bare_rows = isinstance(spec, ServiceSpec) and spec.many and not many_as_argument
    try:
        with translate_dispatch_errors():
            yield
    except InvalidArguments as exc:
        unwrapped = _bare_rows_error(exc, spec.many_argument, params) if bare_rows else None
        if unwrapped is None:
            raise
        raise unwrapped from exc
    except (ServiceValidationError, ValidationError) as exc:
        serializer = getattr(spec, "input_serializer", None)
        if serializer is None:
            raise
        raise wire_named_error(exc, describe_serializer(serializer)) from exc


def _bare_rows_error(
    exc: InvalidArguments, many_argument: str, params: Any
) -> ValidationError | None:
    """``exc`` in the shape a list body is refused in, or ``None`` to leave it keyed.

    A refusal of anything beside the rows - a declared read the route left out -
    names something the caller did send, so it keeps its key.
    """
    detail: Any = exc.detail
    if set(detail) != {many_argument}:
        return None
    rows = detail[many_argument]
    if isinstance(rows, dict):
        # Rows are refused by index only once the body is a list of them.
        return ValidationError([rows.get(index, {}) for index in range(len(params))])
    return ValidationError({api_settings.NON_FIELD_ERRORS_KEY: rows})


# The calling view. A transport hands it to a provider (``kwargs=``, the
# ``get_*_kwargs`` hooks, the serializer-context providers) and puts it in no
# callable's pool, so a selector or service that wants it takes it from one of
# those; a selector or a target lookup can also take it from a route capture of
# that name, which reaches no service's pool. The input schema hides it from a
# selector's parameters, as it hides ``request`` and ``user``, so a caller is never
# told it may send one.
#
# It is not a reserved seed, deliberately. A reserved name is exempt from
# unknown-argument accounting, so ``REJECT`` would drop a caller's ``view`` in
# silence rather than refuse it; and ``RESERVED_POOL_SEEDS`` is public, read by
# the transport adapters to refuse tool-argument names, by ``PoolSeeds`` to refuse
# a registration, and by ``view_url_kwargs`` to strip route captures, so adding it
# there would change all three. Dispatch instead treats it as every callable
# marking it ``NotClientInput`` (``hidden_input_keys``).
TRANSPORT_ONLY_NAMES: frozenset[str] = frozenset({"view"})


def hidden_input_keys(fn: Callable[..., Any]) -> frozenset[str]:
    """The names of ``fn`` a caller never supplies: its ``NotClientInput`` keys and ``view``.

    One callable's share of ``server_owned_keys``, which is what dispatch acts on:
    a key one callable hides is the server's for every callable in the call.
    """
    return marked_input_keys(fn)[1] | TRANSPORT_ONLY_NAMES


def server_owned_keys(spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any]) -> frozenset[str]:
    """Every name no caller supplies anywhere in ``spec``'s dispatch.

    The union of ``hidden_input_keys`` over each callable a dispatch of ``spec``
    feeds from the caller's input: the selector or service, each of
    ``spec.preconditions``, and a service's target lookup
    (``instance_selector_spec`` / ``collection_selector_spec``). **A key any one of
    them marks ``NotClientInput`` is server-owned for the whole call**, because
    the callables share what the caller sent: a precondition reads the pool the
    selector or service is spread into, and a lookup reads the same arguments a
    spread service takes. Hiding a key on one callable while another delivered
    the caller's value for it would let that value decide what the marker said
    only the server decides, such as which tenant a gate checks, or which tenant a
    ``setattr`` loop over an open ``**changes`` writes onto the row.

    Dispatch never takes the caller's value for one: the compiled spec reads no
    ``NotClientInput`` name, so the kernel's ``dispatch`` drops it under ``IGNORE``
    and refuses it under ``REJECT``. This set is the same answer for a transport
    that reads the spec without dispatching it: it is not declared input
    (``declared_input_keys``), and ``spec_to_json_schema`` does not list it.

    **The set governs the keyword pool, not an input serializer's fields.** A
    field the ``input_serializer`` declares under an owned name stays client input:
    the schema lists it, ``REJECT`` admits it, and the caller's value is validated
    into ``data``, the one place it reaches the service. The two may mean
    different things, such as a destination tenant in ``data`` beside the server's
    current tenant at the gate, so the declaration is not refused.

    A provider, a registered pool seed, ``input_data`` and the parameter's default
    still fill it, and so does a route capture for a selector, its preconditions
    or a service's target lookup, the pools a route capture reaches.

    **Public, for a transport building an input schema of its own**, such as a
    service tool that merges its target lookup's keys into the service's: subtract
    this set, less the input serializer's field names, from what it lists, so it
    advertises no key dispatch then drops and keeps every field the serializer
    validates, and pass the spec it dispatches, the service's rather than the
    nested lookup's, or a key the service or one of its preconditions hides is
    missed. ``spec_to_json_schema`` already does both.

    Each callable is one part of the union, and
    ``test_a_service_owns_what_its_service_preconditions_and_lookup_hide`` names a
    key from each, so it fails without any one of them.
    """
    owned: set[str] = set(TRANSPORT_ONLY_NAMES)
    callables: list[Callable[..., Any] | None] = [
        spec.selector if isinstance(spec, SelectorSpec) else spec.service,
        *(spec.preconditions or ()),
    ]
    if isinstance(spec, ServiceSpec):
        lookup = _target_lookup(spec)
        callables.append(None if lookup is None else lookup.selector)
    for fn in callables:
        if fn is not None:
            owned |= hidden_input_keys(fn)
    return frozenset(owned)


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


class _UnresolvedExtras(Exception):
    """A ``**kwargs`` annotation that exists but cannot be resolved at runtime.

    Distinguishes "this callable accepts anything" (a bare ``**kwargs``, genuinely
    open) from "we cannot tell what this callable accepts" — which used to collapse
    into the same ``None``, silently turning a strict policy into a no-op. Raised by
    ``_callable_param_names`` and handled in ``resolve_unknown_arguments``.
    """

    def __init__(self, fn: Callable[..., Any], parameter: str, cause: Exception) -> None:
        label = getattr(fn, "__qualname__", repr(fn))
        super().__init__(
            f"{label}(**{parameter}) is annotated, but the annotation cannot be "
            f"resolved at runtime ({type(cause).__name__}: {cause})."
        )


def _callable_param_names(fn: Callable[..., Any]) -> set[str] | None:
    """Declared keyword-acceptable parameter names of ``fn``; ``None`` if open.

    A bare ``**kwargs`` is open — it accepts anything, so no key can be called
    "unknown". A ``**kwargs: Unpack[SomeExtras]`` is *not*: the ``TypedDict``
    names an exact keyword surface, and only those keys join the declared set.

    An **annotated** ``**kwargs`` whose annotation does not resolve — the
    ``TypedDict`` imported under ``if TYPE_CHECKING:``, which
    ``from __future__ import annotations`` makes routine — raises
    ``_UnresolvedExtras`` rather than reporting the callable as open: the surface is
    unknown, not unrestricted, and the caller decides what an unknown surface means
    for its policy. Only that annotation is resolved, so another parameter that
    does not resolve leaves the surface known
    (``test_reject_reads_an_unpacked_typed_dict_beside_an_unresolved_name``).

    Keys marked ``NotClientInput`` are excluded as provider-owned and never
    advertised, and so is ``view``, which no caller supplies either
    (``hidden_input_keys``). That is ``fn``'s own share only: ``declared_input_keys``
    subtracts the whole call's ``server_owned_keys``.

    It feeds ``declared_input_keys``, which a transport reads to say which keys a
    spec takes without dispatching it.
    """
    parameters = inspect.signature(fn).parameters
    names: set[str] = set()
    for name, p in parameters.items():
        if p.kind is inspect.Parameter.VAR_KEYWORD:
            if p.annotation is inspect.Parameter.empty:
                return None
            try:
                # This annotation alone: another parameter that does not resolve
                # says nothing about the keyword surface.
                annotation = resolved_annotation(fn, name)
            except Exception as exc:  # noqa: BLE001 — any resolution failure is opaque
                raise _UnresolvedExtras(fn, name, exc) from exc
            typed_dict = unpack_typed_dict(annotation)
            if typed_dict is None:
                return None
            names |= set(typed_dict_input(typed_dict)[0])
        elif p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY):
            names.add(name)
    return names - hidden_input_keys(fn)


def _selector_consumed_keys(sel_spec: SelectorSpec[Any, Any] | None) -> set[str] | None:
    """Params keys a nested target selector consumes; ``None`` if open.

    A duck-typed ``filter_set`` is open: its fields are opaque to the core.
    """
    if sel_spec is None or sel_spec.selector is None:
        return set()
    if sel_spec.filter_set is not None:
        return None
    return _callable_param_names(sel_spec.selector)


def _spread_parameter_keys(
    spec: ServiceSpec[Any, Any, Any],
    *,
    argument_binding: ArgumentBinding,
    reserved: frozenset[str],
) -> set[str] | None:
    """The service parameters a caller fills on ``spec``; ``None`` if open.

    Non-empty only for a spec that spreads with nothing else to read the caller's
    input: no ``input_serializer``, and a binding that resolves to a ``SPREAD_*``
    mode. There the service's own caller-suppliable parameters
    (``_callable_param_names``: keyword-passable, ``Unpack[TypedDict]`` keys, no
    ``NotClientInput`` and no ``view``) are the input, less the reserved seeds
    client input can never carry. A bare ``**kwargs`` is open, and an unresolvable
    annotated one raises ``_UnresolvedExtras``.

    Each condition is one conjunct of a single branch arc, which coverage cannot
    see, so each names the test that fails without it:

    - no ``input_serializer``: with one, the serializer declares the input and a
      parameter it does not name stays unknown.
      ``test_a_serializer_keeps_its_set_under_a_spreading_binding``.
    - a ``SPREAD_*`` binding: ``BUNDLE`` (and ``AUTO``, which resolves to it for a
      service) declares none of the service's parameters.
      ``test_without_a_spreading_binding_the_set_is_the_lookup``.
    - less ``reserved``: a seed's name is never the caller's to send.
      ``test_a_registered_seed_leaves_the_set_too``.
    """
    binding = resolve_argument_binding(spec, argument_binding)
    if spec.input_serializer is not None or binding is ArgumentBinding.BUNDLE:
        return set()
    names = _callable_param_names(spec.service)
    return None if names is None else names - reserved


def _target_lookup(spec: ServiceSpec[Any, Any, Any]) -> SelectorSpec[Any, Any] | None:
    """The one target lookup a dispatch of ``spec`` calls.

    A declared ``collection_selector_spec``; otherwise the ``instance_selector_spec``,
    or ``None``. An instance lookup beside a collection one is refused at
    construction, so the order only says which one is read.
    """
    if spec.collection_selector_spec is not None:
        return spec.collection_selector_spec
    return spec.instance_selector_spec


def declared_input_keys(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any],
    *,
    serializer: Any,
    argument_binding: ArgumentBinding = ArgumentBinding.AUTO,
    reserved: frozenset[str] = RESERVED_POOL_SEEDS,
) -> set[str] | None:
    """The set of ``params`` keys ``spec`` declares as input, or ``None`` if open.

    Derived from the spec and the dispatch's ``argument_binding`` — no transport
    knowledge. A ``ServiceSpec`` declares its ``input_serializer`` fields plus the
    keys of the **one** target lookup dispatch calls, so a key is admitted only
    when something reads it:

    - ``many=True`` resolves no target at all, and needs no branch of its own to
      say so: either lookup beside ``many`` is refused when the spec is built, so
      there is no lookup to read. What is left is the item serializer's fields.
      Held by ``test_service_many_declares_no_lookup_keys``.
    - A declared ``collection_selector_spec`` is the lookup; an instance lookup
      beside it is refused at construction.
    - Otherwise ``instance_selector_spec`` (e.g. the ``pk`` it reads). Held by
      ``test_service_serializer_fields_plus_nested``.

    A spec with no ``input_serializer`` dispatched under a ``SPREAD_*`` binding also
    declares its service's own parameters (``_spread_parameter_keys``), because
    nothing else reads the caller's input there; a bare ``**kwargs`` opens the set
    (``test_a_bare_var_keyword_opens_the_set``).
    ``argument_binding`` defaults to ``AUTO``, which is ``BUNDLE`` for a service, so
    a caller that does not pass it gets the set without them. The lookup is read
    first: when it is open, the set is open whatever the service declares, and the
    service's surface is not read, so an unresolvable one raises nothing. Held by
    ``test_an_open_lookup_leaves_the_set_open_before_the_service_is_read``.

    Read from the spec alone, because it is also what a transport advertises, so a
    direct caller passing ``instance=``, which skips the lookup, still has that
    lookup's keys admitted. No view passes an ``instance`` where the two differ.

    **A parameter no callable in the call lets the caller fill is not declared**:
    the selector's, the lookup's and the service's parameters lose
    ``server_owned_keys``, so a key a precondition or the lookup hides is unknown
    even where the selector or service names it plainly. An
    ``input_serializer``'s fields are not reduced: a field is that serializer's
    payload and reaches ``data`` as sent. Held, for a selector, by
    ``test_selector_loses_a_key_its_precondition_hides`` and, for a service, by
    ``test_a_key_another_callable_in_the_call_hides_is_not_listed``.

    ``None`` means the set is not enumerable, so nothing can be flagged unknown.
    Propagates ``_UnresolvedExtras`` when a callable it reads has a ``**kwargs``
    annotation that cannot be resolved — "unknown surface", which is not the same as
    "open".
    """
    owned = server_owned_keys(spec)
    if isinstance(spec, SelectorSpec):
        if spec.filter_set is not None:
            return None
        names = _callable_param_names(spec.selector) if spec.selector is not None else set()
        return None if names is None else names - owned
    declared: set[str] = set(serializer.fields) if serializer is not None else set()
    consumed = _selector_consumed_keys(_target_lookup(spec))
    if consumed is None:
        return None
    parameters = _spread_parameter_keys(spec, argument_binding=argument_binding, reserved=reserved)
    if parameters is None:
        return None
    return declared | ((consumed | parameters) - owned)


def resolve_unknown_arguments(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any],
    params: Mapping[str, Any],
    *,
    unknown_arguments: UnknownArguments,
    serializer: Any,
    reserved: frozenset[str] = RESERVED_POOL_SEEDS,
    argument_binding: ArgumentBinding = ArgumentBinding.AUTO,
) -> dict[str, Any]:
    """Enforce the unknown-argument policy; return ``PASSTHROUGH`` extras (else ``{}``).

    ``PASSTHROUGH`` returns the undeclared key/values so the caller can fold them
    into the dispatched callable's input. Reserved pool seeds are never
    considered unknown. ``argument_binding`` is the dispatch's, since it decides
    whether a service's own parameters are declared (see ``declared_input_keys``);
    its ``AUTO`` default leaves them out of a service's set.

    When a callable's ``**kwargs`` annotation cannot be resolved, its accepted keys
    are unknown rather than unrestricted. ``REJECT`` — whose entire purpose is to
    refuse an argument it does not recognise — cannot be honoured against an unknown
    surface, so it raises ``ImproperlyConfigured`` instead of quietly accepting
    everything. The permissive policies keep treating it as open, which is what they
    would do with a bare ``**kwargs`` anyway.
    """
    if unknown_arguments is UnknownArguments.IGNORE:
        return {}
    try:
        declared = declared_input_keys(
            spec, serializer=serializer, argument_binding=argument_binding, reserved=reserved
        )
    except _UnresolvedExtras as exc:
        if unknown_arguments is UnknownArguments.REJECT:
            raise ImproperlyConfigured(
                f"UnknownArguments.REJECT cannot be enforced: {exc} Make the annotation "
                "resolvable at runtime (import the TypedDict normally rather than under "
                "'if TYPE_CHECKING:'), or dispatch with UnknownArguments.IGNORE or "
                "PASSTHROUGH."
            ) from exc
        return {}
    if declared is None:
        return {}
    unknown = {
        key: value for key, value in params.items() if key not in declared and key not in reserved
    }
    if not unknown:
        return {}
    if unknown_arguments is UnknownArguments.REJECT:
        names = ", ".join(repr(key) for key in sorted(unknown))
        raise ValidationError({"non_field_errors": [f"Unexpected argument(s): {names}."]})
    return unknown


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


async def arun_off_loop(fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    """Run a sync callable in Django's thread-sensitive executor and await it.

    For the async render twins, whose serializer and context providers are sync
    and query: called from the event loop, one raises ``SynchronousOnlyOperation``
    the moment it touches the ORM. ``thread_sensitive=True`` puts it in the same
    executor as the kernel's ``adispatch`` runs sync callables in, so they share
    one connection and see the same transaction state as on the sync path.
    """
    return await sync_to_async(fn, thread_sensitive=True)(*args, **kwargs)


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
    walk renames what it can name and never guesses. A collection's error is
    walked without descending a level, in either of the shapes DRF gives one:
    a list with an entry per incoming row, or a mapping keyed by the failing
    rows' indexes. Each row is keyed like the row. An ``int`` key is how the
    second is told apart, and it is unambiguous: a field name is a string.
    """
    if isinstance(detail, dict):
        renamed: dict[Any, Any] = {}
        for key, value in detail.items():
            if isinstance(key, int):
                renamed[key] = _wire_named_detail(value, names)
                continue
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
