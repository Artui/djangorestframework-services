"""Internal helpers shared by the transport-neutral dispatch + render surface."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Collection, Iterable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any, NamedTuple

from asgiref.sync import sync_to_async
from django.core.exceptions import ImproperlyConfigured
from django.db.models import BooleanField, Model, Value
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.settings import api_settings

from rest_framework_services.dispatch.base_serializer_context import base_serializer_context
from rest_framework_services.dispatch.combine_progress import combine_progress
from rest_framework_services.exceptions.service_validation_error import (
    ServiceValidationError,
)
from rest_framework_services.is_async import is_async
from rest_framework_services.selectors.utils import (
    affordance_expression,
    apply_queryset_shaping,
    is_queryset,
)
from rest_framework_services.services.arun_service import arun_service
from rest_framework_services.services.run_service import run_service
from rest_framework_services.types.argument_binding import ArgumentBinding
from rest_framework_services.types.marked_input_keys import marked_input_keys
from rest_framework_services.types.offline_context import OfflineContext
from rest_framework_services.types.reserved_pool_seeds import RESERVED_POOL_SEEDS
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.types.target_guard import TargetGuard
from rest_framework_services.types.typed_dict_input import typed_dict_input
from rest_framework_services.types.unknown_arguments import UnknownArguments
from rest_framework_services.types.unpack_typed_dict import unpack_typed_dict
from rest_framework_services.types.unset import UNSET
from rest_framework_services.types.utils import (
    PER_CALL_POOL_NAMES,
    affordance_alias,
    is_row_condition,
    resolved_annotation,
)
from rest_framework_services.types.view_hooks import ViewHooks
from rest_framework_services.views.utils import resolve_callable_kwargs

# Handed to ``apply_queryset_shaping`` so a misconfiguration names the offending
# spec field.
SELECTOR_SOURCE = "SelectorSpec.selector"
INSTANCE_SOURCE = "ServiceSpec.instance_selector_spec.selector"
COLLECTION_SOURCE = "ServiceSpec.collection_selector_spec.selector"
OUTPUT_SOURCE = "ServiceSpec.output_selector_spec.selector"

# The calling view. A transport hands it to a provider (``kwargs=``, the
# ``get_*_kwargs`` hooks, the serializer-context providers) and puts it in no
# callable's pool, so a selector or service that wants it takes it from one of
# those, or from a route capture of that name. The input schema hides it from a
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

    Three things follow for each name, so they always agree:

    - the caller's value is dropped at every site that spreads caller input, under
      every policy and whatever the binding's precedence (``strip_hidden_inputs``);
    - it is not declared input (``declared_input_keys``), so ``REJECT`` refuses it
      on a closed spec, and ``spec_to_json_schema`` does not list it;
    - one nothing filled is not named as a missing argument (``caller_fillable``),
      since no value the caller sends could fill it.

    A provider, a route capture, a registered pool seed, ``input_data`` and the
    parameter's default still fill it.

    **Public, for a transport building an input schema of its own**, such as a
    service tool that merges its target lookup's keys into the service's: subtract
    this set from what it lists, so it advertises no key dispatch then drops, and
    pass the spec it dispatches, the service's rather than the nested lookup's, or
    a key the service or one of its preconditions hides is missed.
    ``spec_to_json_schema`` already subtracts it.

    Each callable is one part of the union,
    held by its own test in ``tests/dispatch/test_dispatch_hidden_inputs.py``:

    - the selector or service's own keys:
      ``test_a_callers_hidden_key_never_reaches_the_selector``;
    - each precondition's:
      ``test_a_key_a_precondition_hides_is_server_owned_for_the_whole_call``;
    - the target lookup's:
      ``test_a_key_the_lookup_hides_never_reaches_a_service_naming_it``.
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


def strip_reserved_seeds(
    params: Mapping[str, Any], *, reserved: frozenset[str] = RESERVED_POOL_SEEDS
) -> dict[str, Any]:
    """Drop the dispatcher-owned names from a client-supplied mapping.

    ``merge_arguments`` applies this to every spread it performs, but the
    nested target resolutions build their pool directly and must call it
    themselves — skipping it lets a caller-supplied ``user`` / ``request`` /
    ``instance`` outrank the dispatcher's authoritative value.

    ``reserved`` is the set for *this* dispatch — the dispatcher's own names plus
    whatever the caller's
    [`PoolSeeds`][rest_framework_services.types.pool_seeds.PoolSeeds] registered.
    It defaults to the built-ins so a caller with no registry is unaffected.
    """
    return {key: value for key, value in params.items() if key not in reserved}


def strip_hidden_inputs(
    params: Mapping[str, Any],
    owned: Collection[str],
    *,
    server_supplied: Collection[str] = frozenset(),
) -> Mapping[str, Any]:
    """Drop the server-owned keys from a caller's mapping.

    ``owned`` is ``server_owned_keys(spec)``: every key a callable in the call
    marks ``NotClientInput``, and ``view``. The marker says the caller never
    supplies the key, so dispatch removes the caller's value before it can be
    spread into any pool of the call, whatever the ``UnknownArguments`` policy and
    whatever the binding's precedence. Apply it to the caller's mapping only, never
    to the pool: a ``spec.kwargs`` provider, a route capture, a registered pool
    seed and the parameter's default are all still how such a key is filled.

    ``view`` is in every ``owned``, marked or not (see ``TRANSPORT_ONLY_NAMES``):
    no pool carries it and the input schema hides it, so a caller's value would
    otherwise be the only one a selector declaring it ever received, and on
    ``SPREAD_CALLER_WINS`` it would outrank a provider's. Held by
    ``test_a_callers_view_never_reaches_a_selector`` and the tests beside it in
    ``tests/dispatch/test_dispatch_view_input.py``.

    **``server_supplied`` names the keys ``input_data`` wrote into the mapping**, and
    those are kept. ``input_data`` is the spec author's own code, merged onto the
    caller's input before validation with its keys winning (``apply_input_data``),
    so for each of those keys the mapping holds the server's value and no longer
    the caller's: keeping it lets the server's value reach the callable wherever an
    unmarked key's value would, and lets no caller value through. This is the one
    place that decides it, because by the time the mapping reaches a strip the
    merge has made the two indistinguishable. Held by
    ``test_input_data_fills_a_hidden_parameter_of_an_open_spread_service`` and the
    tests beside it in ``tests/dispatch/test_dispatch_hidden_inputs.py``.

    Both cores call it at every site where caller input is spread into a pool --
    a selector's own spread, which its preconditions read too, a single-item
    service's spread and the extras it is handed beyond its input serializer (the
    ``PASSTHROUGH`` extras, and with no serializer under a ``SPREAD_*`` binding the
    caller's values for its own parameters, all of them when it declares a bare
    ``**kwargs``), and the two target lookups, whose pools are built by hand. Each
    passes the same ``owned``, so a key the lookup hides never reaches an open
    service's ``**changes``, and one a precondition hides never reaches the
    precondition through the selector's or service's spread. Only the two service
    sites pass ``server_supplied``: the target lookups resolve before
    ``input_data`` runs, and a ``SelectorSpec`` has no ``input_data``. A
    ``many=True`` service spreads nothing: its items reach it inside the one
    ``data`` list and never as keyword arguments.

    Returns ``params`` itself when it carries none of those keys, which is nearly
    always, so the common case neither copies nor changes the mapping's type.
    """
    hidden = frozenset(owned).difference(server_supplied)
    if hidden.isdisjoint(params):
        return params
    return {key: value for key, value in params.items() if key not in hidden}


def view_url_kwargs(view: Any, *, reserved: frozenset[str] = RESERVED_POOL_SEEDS) -> dict[str, Any]:
    """Route-capture kwargs carried by the (offline) view, reserved seeds stripped.

    On HTTP the selector pool picks these up as ``extra_url_kwargs=view.kwargs``;
    off-HTTP the
    [`OfflineServiceView`][rest_framework_services.types.offline_service_view.OfflineServiceView]
    carries the same mapping but ``dispatch_spec`` has to read it explicitly. Stripping
    the reserved seeds stops a capture named after one clobbering the dispatcher's own
    value."""
    kwargs = getattr(view, "kwargs", None)
    if not kwargs:
        return {}
    return {key: value for key, value in kwargs.items() if key not in reserved}


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
    reserved: frozenset[str] = RESERVED_POOL_SEEDS,
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
    spread = {k: v for k, v in spread_source.items() if k not in reserved}
    if binding is ArgumentBinding.SPREAD_AUTHOR_WINS:
        pool.update(spread)
        pool.update(url)
        pool.update(provider_kwargs)
    else:  # SPREAD_CALLER_WINS
        pool.update(url)
        pool.update(provider_kwargs)
        pool.update(spread)


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

    It feeds the unknown-argument check, and for one spec it also decides delivery:
    a ``SPREAD_*`` service with no ``input_serializer`` takes the caller's input as
    these parameters, so ``service_extras`` lets into its pool the caller's values
    for exactly these names (see ``_spread_parameter_keys``). Everywhere else the
    pool is filled as before, and this only says which keys are unknown.
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
    annotated one raises ``_UnresolvedExtras``; ``service_extras`` decides what an
    open surface takes. A ``many=True`` spec reaches here only under a binding
    that resolves to ``BUNDLE``, and gets the empty set: a ``SPREAD_*`` binding
    beside ``many`` is refused before any item is read
    (``test_a_serializer_less_many_spec_is_never_dispatched_spread``).

    Each condition is one conjunct of a single branch arc, which coverage cannot
    see, so each names the test that fails without it (in
    ``tests/dispatch/test_dispatch_spread_parameters.py``):

    - no ``input_serializer``: with one, the serializer declares the input and a
      parameter it does not name stays unknown.
      ``test_a_serializer_still_declares_the_input_under_reject``.
    - a ``SPREAD_*`` binding: ``BUNDLE`` (and ``AUTO``, which resolves to it for a
      service) spreads nothing, so admitting a parameter would admit a value no
      parameter receives. ``test_a_bundled_service_still_declares_only_the_lookup``.
    - less ``reserved``: the caller's ``user`` or ``data`` would otherwise land in
      the ``data`` the service receives.
      ``test_a_reserved_seed_is_never_taken_from_the_caller``.
    """
    binding = resolve_argument_binding(spec, argument_binding)
    if spec.input_serializer is not None or binding is ArgumentBinding.BUNDLE:
        return set()
    names = _callable_param_names(spec.service)
    return None if names is None else names - reserved


def _keyword_parameters(fn: Callable[..., Any]) -> set[str]:
    """The parameters ``fn`` names that a keyword can fill, ``**kwargs`` aside."""
    passable = (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    return {
        name
        for name, parameter in inspect.signature(fn).parameters.items()
        if parameter.kind in passable
    }


def _target_lookup(spec: ServiceSpec[Any, Any, Any]) -> SelectorSpec[Any, Any] | None:
    """The one target lookup dispatch calls for ``spec``, by ``_resolve_target``'s precedence.

    A declared ``collection_selector_spec``; otherwise the ``instance_selector_spec``,
    or ``None``. An instance lookup beside a collection one is refused at
    construction, so the order only says which one is read.
    """
    if spec.collection_selector_spec is not None:
        return spec.collection_selector_spec
    return spec.instance_selector_spec


def _lookup_parameter_names(spec: ServiceSpec[Any, Any, Any]) -> set[str]:
    """The names the target lookup reads by name.

    Every named keyword parameter, its ``NotClientInput`` ones included. A hidden
    one is still the lookup's key, filled by a provider or a route capture, and
    ``service_extras`` withholds it from an open ``**changes`` like any other, so
    even the server's value, from ``input_data``, reaches the service only by name.
    The caller's value for it is dropped before that (``server_owned_keys``).
    Subtracting the hidden keys here is what let a caller's ``tenant`` into
    ``changes`` once the strip read only the service's own marker; held by
    ``test_a_key_the_lookup_hides_is_withheld_from_an_open_service_by_name``.

    What the lookup reads only through ``**kwargs`` or its ``filter_set`` is not
    named, so it is not here: the core cannot enumerate either. Empty with no
    lookup, or a lookup with no ``selector``, which ``_resolve_instance`` treats as
    none.
    """
    target = _target_lookup(spec)
    selector = None if target is None else target.selector
    if selector is None:
        return set()
    return _keyword_parameters(selector)


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
    keys of the **one** target lookup dispatch calls, chosen by the precedence
    ``_resolve_target`` applies, so a key is admitted only when something reads it:

    - ``many=True`` resolves no target at all (``_dispatch_service_many`` never
      reaches ``_resolve_target``), and needs no branch of its own to say so:
      either lookup beside ``many`` is refused when the spec is built, so there is
      no lookup to read, and ``guard_many_argument_binding`` refuses a ``SPREAD_*``
      binding before an item is read, so the per-item check runs under ``AUTO``,
      which is ``BUNDLE`` for a service and declares none of its parameters. What
      is left is the item serializer's fields. Held by
      ``test_service_many_declares_no_lookup_keys``, through dispatch by
      ``test_reject_refuses_a_lookup_key_inside_an_item``, and the refusal by
      ``test_a_serializer_less_many_spec_is_never_dispatched_spread``.
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
    ``test_an_open_lookup_leaves_reject_enforceable_beside_an_unresolvable_service``.

    Read from the spec alone, because it is also what a transport advertises, so a
    direct caller passing ``instance=``, which skips the lookup, still has that
    lookup's keys admitted. No view passes an ``instance`` where the two differ.

    **A parameter no callable in the call lets the caller fill is not declared**:
    the selector's, the lookup's and the service's parameters lose
    ``server_owned_keys``, so a key a precondition or the lookup hides is unknown
    even where the selector or service names it plainly, and ``REJECT`` refuses
    the value the strip would drop. An ``input_serializer``'s fields are not
    reduced: a field is that serializer's payload and reaches ``data`` as sent,
    while the strip keeps it out of the spread. Held by
    ``test_a_key_a_precondition_hides_is_server_owned_for_the_whole_call`` and
    ``test_a_key_the_lookup_hides_is_refused_beside_a_service_naming_it``.

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


def _unfilled_parameters(fn: Callable[..., Any], pool: Mapping[str, Any]) -> set[str]:
    """Parameters of ``fn`` that must be passed and that ``pool`` does not fill.

    Each condition below is one conjunct of a single branch arc, which coverage
    cannot see, so each names the test that fails without it (in
    ``tests/dispatch/test_unfilled_parameters.py``):

    - a keyword-passable kind: ``**kwargs`` takes whatever arrives and a
      positional-only parameter is never passed by name, so no caller value could
      fill either. ``test_var_keyword_is_never_missing``,
      ``test_a_positional_only_parameter_is_not_reported``. Both passable kinds
      count, since dispatch passes every argument by name:
      ``test_a_positional_or_keyword_parameter_is_missing_too`` holds the
      positional-or-keyword one, which every other test here leaves out.
    - no default. ``test_a_defaulted_parameter_is_never_missing``.
    - absent from the pool. ``test_the_caller_fills_what_the_provider_declined``.
    - not a reserved seed: client input is stripped of those, so naming one would
      ask the caller for a value dispatch throws away. Requiring one is an author
      error, left to fail as one.
      ``test_a_missing_reserved_seed_is_not_reported_as_an_argument``.

    The built-in set is enough, with no ``PoolSeeds.reserved`` to thread through:
    ``base_pool`` resolves every name a project registers into every pool it
    builds, so a registered seed is never absent.

    **Nothing is unfilled when ``fn`` is a wrapper taking ``**kwargs``.**
    ``inspect.signature`` follows ``__wrapped__``, so a ``functools.wraps``
    decorator reports the wrapped function's parameters, while the wrapper is what
    runs and may fill any of them itself, as a decorator injecting a scope does.
    Which ones cannot be read, so none is asked of the caller, and one the wrapper
    leaves out fails as the ``TypeError`` it did before this check. The two
    conjuncts, one branch arc:

    - the signature that runs differs from the one read, so ``fn`` is a wrapper:
      a plain ``**kwargs`` beside a required parameter fills nothing, and the
      parameter is still missing. ``test_a_parameter_beside_var_keyword_is_still_missing``.
    - that signature takes ``**kwargs``: a wrapper naming its own parameters
      receives only what dispatch passes, so it cannot fill one either.
      ``test_a_wrapper_naming_its_own_parameters_is_still_refused``.
    """
    read = inspect.signature(fn)
    try:
        runs = inspect.signature(fn, follow_wrapped=False)
    except (TypeError, ValueError):
        # A ``staticmethod`` or ``classmethod`` object: it has ``__wrapped__`` but
        # no signature of its own, and calling it calls what it wraps, so the
        # signature read is the one that runs. Held by
        # ``test_a_staticmethod_is_read_through_not_skipped``.
        runs = read
    if runs != read and any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in runs.parameters.values()
    ):
        return set()
    passable = (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    return {
        name
        for name, parameter in read.parameters.items()
        if parameter.kind in passable
        and parameter.default is inspect.Parameter.empty
        and name not in pool
        and name not in RESERVED_POOL_SEEDS
    }


class Fillable(NamedTuple):
    """The names a caller could fill at one call site.

    ``declared`` is what the call site lets a caller send, or ``None`` where that
    is open, which makes every name one the caller could send. ``withheld`` comes
    off either way. Built by ``caller_fillable``; ``NOTHING_FILLABLE`` is the call
    site no caller input reaches.
    """

    declared: frozenset[str] | None
    withheld: frozenset[str]

    def admits(self, name: str) -> bool:
        """Whether a caller could fill ``name`` here.

        Asked of a name, not enumerated from a signature, because an open call
        site reaches an ``InputRequired`` key inside an unpacked ``**kwargs``
        ``TypedDict`` as well as a keyword parameter. Two conjuncts, one branch
        arc, each held by a test in ``tests/dispatch/test_unfilled_parameters.py``:

        - not withheld: ``test_a_hidden_parameter_of_an_open_call_is_not_named``.
        - declared, or the site open:
          ``test_a_precondition_parameter_the_service_does_not_declare_is_the_author_s_error``
          holds the declared set, and ``test_an_open_selector_still_refuses_a_marked_typed_dict_key``
          the open reading of a ``TypedDict`` key.
        """
        return name not in self.withheld and (self.declared is None or name in self.declared)


# An affordance condition and the output re-read: no caller input reaches either
# pool, so no parameter of theirs is one a caller could fill.
NOTHING_FILLABLE = Fillable(declared=frozenset(), withheld=frozenset())


def caller_fillable(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any],
    params: Mapping[str, Any],
    *,
    owned: Collection[str],
    serializer: Any = None,
    argument_binding: ArgumentBinding = ArgumentBinding.AUTO,
    reserved: frozenset[str] = RESERVED_POOL_SEEDS,
) -> Fillable:
    """What a caller could still fill for the callables ``spec`` names, given ``params``.

    The rule ``resolve_dispatch_kwargs`` refuses an unfilled parameter by: only a
    name in it is asked of the caller, so a refusal never names a key the caller
    cannot send, or has sent already, and a client retrying on the refusal never
    loops. Anything else unfilled is the author's to fix, and fails as the
    callable's own ``TypeError``, which over HTTP is a ``500``.

    - **Nothing under a binding that resolves to ``BUNDLE``**, for any spec:
      ``BUNDLE`` spreads no caller input into the pool, so a service's input
      arrives only as ``data`` and a selector's not at all.
    - **A ``SelectorSpec``** otherwise, for its selector and preconditions, or for
      a service's target lookup when that is the spec passed: its declared input,
      the parameters the selector names (``_selector_consumed_keys``), all of them
      under a ``filter_set`` or a bare ``**kwargs``. Over HTTP a mutation reads a
      lookup's from the request body.
    - **A ``ServiceSpec``** otherwise, for its service and preconditions:
      ``declared_input_keys`` under the ``SPREAD_*`` binding, every name where
      that is open.

    Less, in every case, ``owned`` (``server_owned_keys``), which no caller value
    reaches, and the keys of ``params``, which arrived wherever they could, so a
    sent key still missing was dropped on the way by something the caller cannot
    change. An unresolvable ``**kwargs`` annotation reads as open, as the
    permissive policies read it; under ``REJECT`` dispatch has raised already.

    ``argument_binding`` is the dispatch's, for a selector as for a service. A
    target lookup is read with the default, which is a ``SPREAD_*`` mode for a
    ``SelectorSpec``, because its pool is spread from the caller's arguments
    whatever binding the service is dispatched under.

    Each part is one arc or one operand, so each names the test that fails
    without it (in ``tests/dispatch/test_unfilled_parameters.py``):

    - empty under ``BUNDLE``, where a declared name exists, for a service:
      ``test_a_bundled_service_parameter_named_like_a_field_is_the_author_s_error``;
      and for a selector, which reads the binding only from its call site:
      ``test_a_bundled_selector_parameter_is_the_author_s_error``.
    - the declared input, not every name:
      ``test_a_precondition_parameter_the_service_does_not_declare_is_the_author_s_error``.
    - every name where open: the ``open-spread-service-precondition`` row of
      ``test_a_refused_name_is_one_the_callers_resend_delivers``.
    - every name where unresolvable:
      ``test_an_unresolvable_extras_annotation_reads_as_open``.
    - less ``owned``: ``test_a_hidden_parameter_of_an_open_call_is_not_named``.
    - less what was sent: ``test_a_sent_key_the_service_never_receives_is_not_named``.
    """
    if resolve_argument_binding(spec, argument_binding) is ArgumentBinding.BUNDLE:
        return NOTHING_FILLABLE
    try:
        if isinstance(spec, SelectorSpec):
            declared = _selector_consumed_keys(spec)
        else:
            declared = declared_input_keys(
                spec, serializer=serializer, argument_binding=argument_binding, reserved=reserved
            )
    except _UnresolvedExtras:
        declared = None
    return Fillable(
        declared=None if declared is None else frozenset(declared),
        withheld=frozenset(owned).union(params),
    )


def resolve_dispatch_kwargs(
    fn: Callable[..., Any], pool: dict[str, Any], *, fillable: Fillable
) -> dict[str, Any]:
    """``resolve_callable_kwargs``, refusing an argument the caller left unfilled.

    Two kinds of name count as missing, and one message lists them all, sorted:

    - an ``InputRequired`` key, marked on a parameter or inside an unpacked
      ``**kwargs`` ``TypedDict``, that nothing filled;
    - a parameter with no default that nothing filled: a read the caller did not
      send, where a ``kwargs=`` provider declined it with ``UNSET`` or there is no
      provider at all. ``**kwargs``, a parameter with a default, a positional-only
      parameter, a reserved seed and any parameter of a wrapper taking
      ``**kwargs`` are never missing (see ``_unfilled_parameters``).

    Either is named only when **the caller could have filled it** (``fillable``,
    from ``caller_fillable``). Anything else unfilled fails as the callable's own
    error, as it did before the check existed, a ``TypeError`` for a parameter and
    whatever reading it raises for a ``TypedDict`` key: a ``NotClientInput`` key
    or ``view``, any name under ``BUNDLE``, one the call site does not declare, and
    one the caller sent that never arrived. Naming any of those would ask for a
    value the caller cannot send, or already did, and a client that retries on a
    validation error, as an agent does, would retry forever. It is a gap in what
    the server supplies, which no client can fix. The marker says the value must
    arrive, and it still does where a caller could send it; where none could, the
    refusal is the retry loop, so the two kinds are asked the same question
    (``test_a_bundled_input_required_key_is_the_author_s_error``).

    Must run against the **fully assembled** pool: any channel (caller params,
    URL kwargs, the ``spec.kwargs`` provider) satisfies either, since the marker
    says the value must arrive, not where from.

    Raises
    [`ServiceValidationError`][rest_framework_services.exceptions.service_validation_error.ServiceValidationError]
    rather than letting the callable raise ``KeyError`` or ``TypeError``, because
    every transport maps the former to a caller-visible validation failure and none
    maps the latter.

    The HTTP views dispatch through the same core, but only a mutation view maps
    ``ServiceValidationError``, to a ``400``. A mutation dispatches its service
    ``BUNDLE``, so a provider or view hook that leaves a service or precondition
    parameter out is not refused, marked ``InputRequired`` or not: it fails as the
    ``TypeError`` it is, a ``500``
    (``test_over_http_a_hook_that_leaves_a_parameter_out_is_a_server_error``,
    ``test_over_http_a_hook_that_leaves_an_input_required_key_out_is_a_server_error``). A
    target lookup parameter nothing fills is refused, with a ``400``, because the
    lookup reads the request body (``test_over_http_a_lookup_parameter_nothing_fills_is_a_400``).
    A selector view dispatches ``BUNDLE`` too, so a selector parameter nothing fills
    is its ``TypeError``, a ``500``
    (``test_over_http_a_selector_view_still_answers_a_server_error``).

    ``NOTHING_FILLABLE`` is for a callable whose pool carries no client input: an
    affordance condition, which sees only the seeds
    (``test_an_affordance_condition_is_not_asked_of_the_caller``), and the output
    re-read, whose pool is the seeds and the service's result. That one also runs
    after the write committed, so a validation error there would tell the caller
    nothing happened, and a caller that retries on one would write twice
    (``test_the_output_re_read_is_not_refused_after_the_write_committed``).
    """
    unfilled = {key for key in marked_input_keys(fn)[0] if key not in pool}
    unfilled |= _unfilled_parameters(fn, pool)
    missing = {key for key in unfilled if fillable.admits(key)}
    if missing:
        names = ", ".join(repr(key) for key in sorted(missing))
        raise ServiceValidationError(
            {"non_field_errors": [f"Missing required argument(s): {names}."]}
        )
    return resolve_callable_kwargs(fn, pool)


def service_extras(
    spec: ServiceSpec[Any, Any, Any],
    params: Mapping[str, Any],
    passthrough: dict[str, Any],
    *,
    argument_binding: ArgumentBinding,
    reserved: frozenset[str],
) -> dict[str, Any]:
    """What a single-item service takes from the caller beyond its input serializer.

    ``passthrough`` is what ``resolve_unknown_arguments`` returned, and is the whole
    answer for every spec but one. A spec with no ``input_serializer`` dispatched
    under a ``SPREAD_*`` binding also takes the caller's values for its service's
    own parameters, since ``declared_input_keys`` admits those names and nothing
    else reads them. ``service_input`` then makes the result both ``data`` and the
    spread, as it does every serializer-less input, so ``data`` mirrors what a
    dict-validating serializer would hand over: the declared names, plus what
    ``PASSTHROUGH`` forwards. The two never share a key, because a declared name is
    never an extra.

    A bare ``**kwargs`` declares every name, so every key the caller sent is taken
    but two kinds. The reserved seeds never are. And **the target lookup's keys
    reach the service only by name**: the lookup consumed the caller's ``pk`` to
    find the ``instance`` the service receives, so ``def update(*, instance,
    **changes)`` does not get it in ``changes``, where applying them would write it
    onto the row, while ``def update(*, instance, pk, **changes)`` asks for it and
    gets it (``_lookup_parameter_names``). An annotated ``**kwargs`` that cannot be
    resolved is taken the same way, the open reading ``resolve_unknown_arguments``
    gives the permissive policies (under ``REJECT`` dispatch has raised before this
    runs). A key the lookup marks ``NotClientInput`` is one of its keys too, so a
    service reaches it only by naming it. The caller then strips the caller's
    values for every server-owned key from the result, as from any extras, and
    keeps the ones ``input_data`` supplied (``strip_hidden_inputs``), so a service
    naming the lookup's hidden ``tenant`` receives the server's value and never the
    caller's.

    Each step names the test that fails without it (in
    ``tests/dispatch/test_dispatch_spread_parameters.py``):

    - the declared names: ``test_a_sent_parameter_is_delivered_under_every_policy``.
    - the ``PASSTHROUGH`` extras beside them:
      ``test_data_carries_the_parameters_beside_the_passthrough_extras``.
    - an unresolvable surface read as open rather than raised:
      ``test_an_unresolvable_var_keyword_is_open_to_the_permissive_policies``.
    - every key from an open surface:
      ``test_a_bare_var_keyword_with_no_lookup_takes_every_argument``.
    - less the reserved seeds and the lookup's keys:
      ``test_a_bare_var_keyword_takes_every_argument_but_the_lookups``.
    - except a lookup key the service names:
      ``test_a_service_naming_the_lookups_key_beside_var_keyword_receives_it``.
    - the dispatch's own ``reserved``, so a registered seed is held out too:
      ``test_a_registered_seed_is_never_taken_from_the_caller``.
    """
    try:
        names = _spread_parameter_keys(spec, argument_binding=argument_binding, reserved=reserved)
    except _UnresolvedExtras:
        names = None
    if names is None:
        withheld = _lookup_parameter_names(spec) - _keyword_parameters(spec.service)
        taken = {
            key: value
            for key, value in strip_reserved_seeds(params, reserved=reserved).items()
            if key not in withheld
        }
    else:
        taken = {key: value for key, value in params.items() if key in names}
    return {**taken, **passthrough}


def service_input(serializer: Any, extras: dict[str, Any]) -> tuple[Any, Mapping[str, Any]]:
    """Return ``(data, spread_source)`` for a service pool, folding in PASSTHROUGH ``extras``.

    ``data`` is what a callable declaring ``data=`` receives; ``spread_source``
    is what the ``SPREAD_*`` binding modes spread as individual kwargs:

    - dict-validated input — ``extras`` merge into both ``data`` and the spread.
    - dataclass-validated input (opaque to the spread) — ``data`` is the
      dataclass instance unchanged; ``extras`` can reach a callable only via the
      spread, so a ``BUNDLE`` dataclass mutation drops them, by design.
    - no ``input_serializer`` — ``data`` is the ``extras`` dict, or ``None``. Under a
      ``SPREAD_*`` binding ``service_extras`` has put the caller's values for the
      service's own parameters there, beside what ``PASSTHROUGH`` forwards.
    """
    validated = serializer.validated_data if serializer is not None else None
    return service_input_for_validated(validated, extras)


def service_input_for_validated(
    validated: Any, extras: dict[str, Any]
) -> tuple[Any, Mapping[str, Any]]:
    """The ``(data, spread_source)`` fold for one already-validated value.

    Split out of ``service_input`` so the ``many=True`` path gets identical
    per-item semantics; see there for the dict / dataclass / no-serializer rules.
    """
    if isinstance(validated, dict):
        data = {**validated, **extras} if extras else validated
        return data, data
    if validated is not None:
        return validated, extras
    return (extras or None), extras


def guard_many_argument_binding(argument_binding: ArgumentBinding) -> None:
    """Reject a spreading ``argument_binding`` on a ``many=True`` dispatch.

    A bulk service is invoked once with the whole validated list as ``data``, so
    the ``SPREAD_*`` modes have no scalar client argument to act on. ``AUTO``
    resolves to ``BUNDLE`` here, and ``BUNDLE`` is exactly what a list payload does,
    so both are allowed: refusing the explicit name for the behaviour that runs
    would make a caller whose own default is ``BUNDLE`` special-case every
    ``many=True`` spec, and a caller that forgot one path would fail only there.
    """
    # One branch arc for both members; ``BUNDLE`` is held by
    # test_an_explicit_bundle_binding_is_accepted.
    if argument_binding not in (ArgumentBinding.AUTO, ArgumentBinding.BUNDLE):
        raise ValueError(
            "argument_binding is not applicable with many=True: a bulk service "
            "receives the whole list as `data`, so there are no scalar client "
            "arguments to spread. Pass a SPREAD_* binding only on single-item specs."
        )


def many_argument_items(spec: ServiceSpec[Any, Any, Any], params: Any) -> Any:
    """The list a ``many=True`` spec validates, read out of an object of arguments.

    Refuses what the documented workaround -- a wrapper serializer declaring
    ``<many_argument> = Item(many=True)`` -- refuses before any item is looked at, in
    DRF's own words, codes and nesting, so a client moving between the two sees the
    same error: arguments that are not an object, the argument missing, ``null``, or
    not a list. A non-list is refused here rather than left to the list serializer,
    because a spec with no ``input_serializer`` has no list serializer to refuse it
    and would iterate a string.
    """
    if not isinstance(params, Mapping):
        raise ValidationError(
            {
                api_settings.NON_FIELD_ERRORS_KEY: [
                    serializers.Serializer.default_error_messages["invalid"].format(
                        datatype=type(params).__name__
                    )
                ]
            },
            code="invalid",
        )
    name: str = spec.many_argument
    if name not in params:
        raise ValidationError(
            {name: [serializers.Field.default_error_messages["required"]]}, code="required"
        )
    items: Any = params[name]
    if items is None:
        raise ValidationError(
            {name: [serializers.Field.default_error_messages["null"]]}, code="null"
        )
    if not isinstance(items, list):
        raise ValidationError(
            {
                name: {
                    api_settings.NON_FIELD_ERRORS_KEY: [
                        serializers.ListSerializer.default_error_messages["not_a_list"].format(
                            input_type=type(items).__name__
                        )
                    ]
                }
            },
            code="not_a_list",
        )
    return items


def refuse_arguments_beside_many(
    spec: ServiceSpec[Any, Any, Any],
    params: Mapping[str, Any],
    *,
    reserved: frozenset[str] = RESERVED_POOL_SEEDS,
) -> None:
    """Refuse any argument sent beside a ``many=True`` spec's list.

    Whatever ``unknown_arguments`` says, because nothing beside the list has anywhere
    to go: the service receives the list as its one ``data``, so ``IGNORE`` would drop
    the argument without a trace and ``PASSTHROUGH`` has no slot to put it in. The
    policy still governs the keys inside each item. The wording, the reserved-seed
    exemption and the shape are ``UnknownArguments.REJECT``'s, which is what the
    workaround answers the same call with under that policy.
    """
    # Held by test_the_list_under_the_default_argument_reaches_the_service (the name
    # half) and test_a_reserved_seed_beside_the_list_is_not_an_unexpected_argument
    # (the reserved half).
    unexpected = sorted(key for key in params if key != spec.many_argument and key not in reserved)
    if unexpected:
        names = ", ".join(repr(key) for key in unexpected)
        raise ValidationError({"non_field_errors": [f"Unexpected argument(s): {names}."]})


@contextmanager
def many_argument_errors(name: str | None) -> Iterator[None]:
    """Key a list's validation errors under the argument it arrived as.

    ``name`` is ``None`` for a caller that sent the bare list, whose errors pass
    through as DRF raised them -- the HTTP body keeps the shape it always had.

    Otherwise item errors are normalised to one shape before keying. DRF below 3.18
    reports them as a list with an empty entry for every valid item; from 3.18 as a
    mapping of the invalid items' indexes, as ``int`` keys. The mapping is what the
    workaround produces on current DRF, and it names the failing rows without making
    a client count past the valid ones, so a list becomes that mapping. Only item
    errors are ever a list: the list serializer's own refusals (not a list, empty,
    out of bounds) are a mapping under the non-field key on every version, and are
    keyed unchanged.
    """
    if name is None:
        yield
        return
    try:
        yield
    except ValidationError as exc:
        detail: Any = exc.detail
        # Held on the locked DRF by
        # test_item_errors_reported_as_a_list_are_keyed_by_index_too, whose list
        # serializer reports the older shape; on the floor, by every item-error test.
        if isinstance(detail, list):
            detail = {index: errors for index, errors in enumerate(detail) if errors}
        # Annotated because the stubs admit only ``str`` keys in a detail, while
        # the ``int``-keyed mapping above is the one DRF 3.18 raises itself.
        keyed: dict[str, Any] = {name: detail}
        raise ValidationError(keyed) from exc


def guard_mapping_params(params: Any) -> None:
    """Reject a non-mapping ``params`` on a single-item dispatch.

    Only a ``many=True`` spec takes a list payload; every other path spreads
    ``params`` into a keyword pool and validates it as one object, which needs
    ``.items()``. A JSON array — or any other non-object — reaching a
    single-item spec is client input, so it earns a 400 here rather than the
    ``AttributeError`` the first spread would raise, which is not an
    ``APIException`` and so surfaces as a 500 with a stack trace.
    """
    if not isinstance(params, Mapping):
        raise ValidationError(
            {
                "non_field_errors": [
                    "Invalid data. Expected a dictionary, but got "
                    f"{type(params).__name__}. Only a spec declaring many=True "
                    "accepts a list payload."
                ]
            }
        )


def resolve_service_many_input(
    spec: ServiceSpec[Any, Any, Any],
    serializer: Any,
    params: list[Any],
    *,
    unknown_arguments: UnknownArguments,
    reserved: frozenset[str] = RESERVED_POOL_SEEDS,
    index_errors: bool = False,
) -> tuple[list[Any] | None, bool]:
    """Assemble the ``data`` list for a ``many=True`` dispatch, honouring
    ``unknown_arguments`` **per list element**.

    Each raw item is checked against the *child* serializer's fields, so
    ``REJECT`` raises on the first offending item. ``has_data`` is ``False`` only
    for the degenerate no-serializer / no-extras case, where the pool must omit
    ``data`` entirely — exactly as the single-item path omits it.

    ``index_errors`` keys that refusal by the item's index, the shape
    ``many_argument_errors`` gives item validation errors, so a caller whose list
    arrived as an argument learns which row to fix from either refusal. Off by
    default: the bare-list path keeps the shape it always had.
    """
    child = serializer.child if serializer is not None else None
    validated = serializer.validated_data if serializer is not None else None
    data_items: list[Any] = []
    has_data = serializer is not None
    for index, raw_item in enumerate(params):
        try:
            extras = resolve_unknown_arguments(
                spec,
                raw_item,
                unknown_arguments=unknown_arguments,
                serializer=child,
                reserved=reserved,
            )
        except ValidationError as exc:
            if not index_errors:
                raise
            # An ``int`` key, as DRF's own list serializer gives item errors from 3.18;
            # the stubs type a detail mapping's keys as ``str`` only.
            keyed: Any = {index: exc.detail}
            raise ValidationError(keyed) from exc
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


def ambient_pool(pool: Mapping[str, Any], *, reserved: frozenset[str]) -> dict[str, Any]:
    """The part of a pool an affordance's callable condition may read.

    The seeds -- ``user``, ``request``, ``progress`` and whatever the project
    registered -- and nothing else. Not the per-call names (the target and the
    validated input), which would make the answer depend on attempting the call;
    and not a client argument spread into the pool, which would let the caller
    decide whether the operation is available. Filtering by the reserved set
    rather than by exclusion is what makes the second hold: a spread key is by
    construction not a seed.

    One definition, so a condition asked at the moment of a call and the same
    condition projected onto a list see the same names.
    """
    return {
        key: value
        for key, value in pool.items()
        if key in reserved and key not in PER_CALL_POOL_NAMES
    }


def answer_operation_condition(
    when: Callable[..., Any], pool: Mapping[str, Any], *, reserved: frozenset[str]
) -> bool:
    """One callable affordance condition, answered against ``pool`` and read for truth.

    Three places ask a condition on nothing in particular: the call, refusing it
    (``enforce_affordances``); a list, projecting it onto every row
    (``split_affordances``); and a transport deciding whether to offer the
    operation at all (``unmet_operation_affordance``). All three answer it here,
    so they cannot disagree about which names the condition sees -- the
    ``ambient_pool`` of whatever pool the caller holds, taken here rather than by
    each caller -- nor about what counts as met: a callable that returns nothing
    is unmet in all three, not in two of them.
    """
    ambient = ambient_pool(pool, reserved=reserved)
    # No client input reaches the ambient pool, so a parameter it does not fill is
    # the author's; refusing it would ask the caller for a value they cannot send.
    return bool(when(**resolve_dispatch_kwargs(when, ambient, fillable=NOTHING_FILLABLE)))


def split_affordances(
    affordances: Mapping[str, ServiceSpec[Any, Any, Any]],
    pool: Mapping[str, Any],
    *,
    reserved: frozenset[str],
) -> tuple[dict[str, Any], dict[str, bool]]:
    """``(row conditions, answered constants)``, both keyed by annotation name.

    A condition on the row is returned as declared, for whichever path evaluates
    it -- an annotation on a query, or one query over the rows a selector returned
    -- and both build it with ``affordance_expression``, the correlated ``Exists``
    the single-object check also runs, so every path agrees about every row by
    construction rather than by a test. A callable condition has no row to vary
    with, so it is answered once, here, by the same ``answer_operation_condition``
    the call is refused by, whatever the selector returned.
    """
    row_conditions: dict[str, Any] = {}
    constants: dict[str, bool] = {}
    for name, service_spec in affordances.items():
        for affordance in service_spec.affordances or ():
            alias = affordance_alias(name, affordance.code)
            if is_row_condition(affordance.when):
                row_conditions[alias] = affordance.when
                continue
            constants[alias] = answer_operation_condition(affordance.when, pool, reserved=reserved)
    return row_conditions, constants


def rows_with_affordances(
    result: Any,
    *,
    kind: SelectorKind,
    row_conditions: Mapping[str, Any],
    constants: Mapping[str, bool],
    source_label: str,
) -> Any:
    """A selector's non-``QuerySet`` result, with each row carrying its answers.

    The same answers, under the same ``affordance__<name>__<code>`` names, that a
    ``QuerySet`` result carries as annotations -- so rendering reads them the same
    way whichever the selector returned. ``RETRIEVE`` treats the result as one row
    (``None`` passes through); ``LIST`` as an iterable of rows, materialised into
    the list that flows on, so a generator is walked once.

    - **A model instance** gets every row condition answered by one query per
      model class present -- see ``_row_condition_answers`` -- and the answers set
      as attributes, like an annotation would be.
    - **A mapping** gets a new mapping with the callable answers added; the
      selector's own object is left alone. A condition on the row is refused
      here, because a mapping has no model and no primary key to evaluate one
      against.
    - **Anything else** is refused rather than having attributes written onto it.

    Raises:
        ImproperlyConfigured: A ``LIST`` result is not an iterable of rows, a row is
            neither a model instance nor a mapping, a row condition meets a mapping
            row, or a row condition meets an instance with no primary key.
    """
    if kind is SelectorKind.RETRIEVE:
        if result is None:
            return None
        return _answer_rows([result], row_conditions, constants, source_label)[0]
    if isinstance(result, Mapping | str | bytes) or not isinstance(result, Iterable):
        raise ImproperlyConfigured(
            f"affordances are declared on a LIST spec but {source_label} returned "
            f"{type(result).__name__}, which is neither a QuerySet nor an iterable of rows."
        )
    return _answer_rows(list(result), row_conditions, constants, source_label)


def _answer_rows(
    rows: list[Any],
    row_conditions: Mapping[str, Any],
    constants: Mapping[str, bool],
    source_label: str,
) -> list[Any]:
    by_model: dict[type[Model], list[Any]] = {}
    for row in rows:
        if isinstance(row, Model):
            if row_conditions and row.pk is None:
                raise ImproperlyConfigured(
                    f"{source_label} returned {row!r}, which has no primary key, and a "
                    "condition on the row is answered by finding the row. Return saved "
                    "instances."
                )
            by_model.setdefault(type(row), []).append(row.pk)
        elif isinstance(row, Mapping):
            if row_conditions:
                raise ImproperlyConfigured(
                    f"{source_label} returned mapping rows, and the affordances declare a "
                    "condition on the row: a mapping has no model and no primary key to "
                    "evaluate one against. Return model instances or a QuerySet, or keep "
                    "only callable conditions."
                )
        else:
            raise ImproperlyConfigured(
                f"{source_label} returned a row of type {type(row).__name__}. Affordance "
                "answers are carried by model instances and mappings; return one of those, "
                "or a QuerySet."
            )
    answers = (
        {
            model: _row_condition_answers(model, pks, row_conditions)
            for model, pks in by_model.items()
        }
        if row_conditions
        else {}
    )
    answered: list[Any] = []
    for row in rows:
        if isinstance(row, Mapping):
            answered.append({**row, **constants})
            continue
        flags = answers.get(type(row), {}).get(row.pk, {})
        for alias in row_conditions:
            # ``None`` for a row the answers query did not find -- see
            # ``_row_condition_answers`` for why that is not ``False``.
            setattr(row, alias, flags.get(alias))
        for alias, answer in constants.items():
            setattr(row, alias, answer)
        answered.append(row)
    return answered


def _row_condition_answers(
    model: type[Model], pks: list[Any], row_conditions: Mapping[str, Any]
) -> dict[Any, dict[str, bool]]:
    """Every row condition's answer for every ``pk``, in one query over ``model``.

    Annotated with ``affordance_expression`` -- the same expression a ``QuerySet``
    result is annotated with and the call is checked with -- and narrowed with
    ``_base_manager``, because the rows are already in hand and a default manager
    that hides some would answer for fewer of them.

    **A pk the table no longer holds is absent from the result**, and its row
    carries ``None`` for every row condition, not ``False``. The row was deleted
    between the selector returning it and this query, so nothing can be done to
    it -- a call against it is refused as not found -- and it must not read as
    available. But ``False`` means "fails this condition", and the rendered
    answer for a failed condition names its code and reason: "already published"
    is a false sentence about a row that no longer exists. ``None`` is the third
    answer, "no row to ask", which renders as unavailable with no code and no
    reason. Callable conditions are not about the row, and keep their real
    answers.
    """
    aliases = list(row_conditions)
    found = (
        model._base_manager.filter(pk__in=pks)
        .annotate(
            **{alias: affordance_expression(model, when) for alias, when in row_conditions.items()}
        )
        .values_list("pk", *aliases)
    )
    return {
        pk: {alias: bool(flag) for alias, flag in zip(aliases, flags, strict=True)}
        for pk, *flags in found
    }


def call_preconditions(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any],
    pool: dict[str, Any],
    *,
    fillable: Fillable,
) -> None:
    """Run a spec's ``preconditions`` through the keyword pool, in order.

    Callers must invoke this **after** validation and target resolution and
    **before** the service: permissions → target resolution → validation →
    preconditions → service. That ordering is what lets a state rule over the
    resolved row and a coherence rule over the validated payload share one pool,
    and it keeps business logic off an unvalidated payload. Raise-to-abort — the
    return value is ignored, so a predicate written ``-> bool`` returning
    ``False`` is silently a no-op.

    ``fillable`` is the selector's or service's (``caller_fillable``): a
    precondition reads the same pool, so the caller could fill exactly what the
    callable it guards lets the caller fill.
    """
    for precondition in spec.preconditions or ():
        precondition(**resolve_dispatch_kwargs(precondition, pool, fillable=fillable))


async def acall_preconditions(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any],
    pool: dict[str, Any],
    *,
    fillable: Fillable,
) -> None:
    """``call_preconditions`` for the async path.

    Preconditions are sync-only, like every auxiliary callable a spec carries:
    only ``selector`` and ``service`` may be ``async def``, because a spec is
    written once for both transports and an ``async def`` precondition would be
    un-callable on the sync path. Assumed to query, hence the executor.
    """
    for precondition in spec.preconditions or ():
        await arun_off_loop(
            precondition, **resolve_dispatch_kwargs(precondition, pool, fillable=fillable)
        )


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

    The provider is user code that may query — a task record is found or created
    before any report is sent — so ``adispatch_spec`` runs this whole function
    through ``arun_off_loop``, as it does every provider a spec carries.
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

    A key whose value is ``UNSET`` is dropped: the
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
    pool: Mapping[str, Any],
    reserved: frozenset[str] = RESERVED_POOL_SEEDS,
) -> Any:
    """Apply a selector spec's queryset shaping, ``params`` as the filter data.

    ``spec.affordances`` on a ``QuerySet`` join ``spec.annotations`` in the one
    ``.annotate()`` call the shaping makes. On any other result -- a list of rows,
    or a ``RETRIEVE`` selector's bare row -- the rows are answered after the
    shaping, by ``rows_with_affordances``. ``pool`` is the pool the selector was
    called with, from which a callable condition reads its seeds.
    """
    annotations: Mapping[str, Any] | None = spec.annotations
    row_conditions: dict[str, Any] = {}
    constants: dict[str, bool] = {}
    queryset = is_queryset(qs)
    if spec.affordances is not None:
        row_conditions, constants = split_affordances(spec.affordances, pool, reserved=reserved)
        if queryset:
            generated: dict[str, Any] = {
                **{
                    alias: affordance_expression(qs.model, when)
                    for alias, when in row_conditions.items()
                },
                **{
                    alias: Value(answer, output_field=BooleanField())
                    for alias, answer in constants.items()
                },
            }
            if generated:
                annotations = {**(annotations or {}), **generated}
    shaped = apply_queryset_shaping(
        qs,
        view,
        request,
        select_related=spec.select_related,
        prefetch_related=spec.prefetch_related,
        annotations=annotations,
        extend_queryset=spec.extend_queryset,
        filter_set=spec.filter_set,
        filter_data=params,
        source_label=source_label,
    )
    if spec.affordances is None or queryset:
        return shaped
    return rows_with_affordances(
        shaped,
        kind=spec.kind,
        row_conditions=row_conditions,
        constants=constants,
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

    The input-phase twin of ``resolve_output_context``, and same layering
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
    serializer-context providers, the ``progress_reporter`` provider, a callable
    ``success_status``. None of them may be async, so calling one from the event
    loop raises
    ``SynchronousOnlyOperation`` the moment it touches the ORM — a failure that
    surfaces only under the async transport, only for specs that happen to query.
    ``thread_sensitive=True`` puts them in the same executor as the surrounding
    selector / service / permission calls, so they share one connection and see
    the same transaction state as on the sync path.

    Not for the spec's own selector / service, which may be ``async def``; those
    go through ``arun_callable`` / ``arun_service_callable``.
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


def service_return_as_list(spec: ServiceSpec[Any, Any, Any], result: Any) -> Any:
    """``result``, once it is a set of rows a ``LIST`` declaration can present.

    The declaration says the service returns the set, so a return that cannot be
    one is the author's error: a mapping, a ``str`` or ``bytes`` (iterable, but by
    key or character rather than by row), anything else that does not iterate, and
    ``None``, since a list is empty rather than absent. It is raised as
    ``ImproperlyConfigured``, as every other declaration dispatch finds it cannot
    honour is, an affordance answered over a ``LIST`` result that is no set of rows
    among them. That is not a refusal: the service has run and its write stands,
    so it must not reach a caller as a validation error saying nothing happened.

    The test is one arc to coverage, so each member is a row of
    ``test_a_return_that_is_not_a_set_of_rows_is_the_authors_error``, which fails
    without it: ``mapping``, ``str``, ``bytes``, ``non-iterable`` and ``none``. Both
    cores call it, and it runs no query, so the async core calls it on the event
    loop; ``test_a_refused_list_return_reads_the_same_on_either_core`` holds the
    two to one message.
    """
    if isinstance(result, Mapping | str | bytes) or not isinstance(result, Iterable):
        label = getattr(spec.service, "__qualname__", repr(spec.service))
        raise ImproperlyConfigured(
            "output_selector_spec declares kind=LIST with no selector, so the service's "
            f"own return is the list presented, and {label} returned "
            f"{type(result).__name__}, which is neither a QuerySet nor an iterable of "
            "rows. Return the rows, or declare kind=SelectorKind.RETRIEVE to present one "
            "value. The service has already run, so its write stands."
        )
    return result
