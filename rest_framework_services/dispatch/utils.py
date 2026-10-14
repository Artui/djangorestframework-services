"""Internal helpers shared by the transport-neutral dispatch + render surface."""

from __future__ import annotations

import dataclasses
import inspect
import threading
import weakref
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from types import MappingProxyType
from typing import Any, cast

from asgiref.sync import sync_to_async
from django.core.exceptions import ImproperlyConfigured
from django.http import QueryDict
from django.utils.module_loading import import_string
from django_service_specs.adapters.filters.filter_set_parameters import filter_set_parameters
from django_service_specs.dispatch.check_call import check_call
from django_service_specs.dispatch.grant import Grant
from django_service_specs.dispatch.resolve_target import resolve_target
from django_service_specs.parameters.coerce_flat import coerce_flat
from django_service_specs.parameters.invalid_arguments import (
    InvalidArguments as KernelInvalidArguments,
)
from django_service_specs.parameters.parameters import Parameters
from django_service_specs.specs.selector_spec import SelectorSpec as KernelSelectorSpec
from django_service_specs.specs.service_spec import ServiceSpec as KernelServiceSpec
from rest_framework.exceptions import ErrorDetail, ValidationError
from rest_framework.settings import api_settings

from rest_framework_services.dispatch.base_serializer_context import base_serializer_context
from rest_framework_services.dispatch.combine_progress import combine_progress
from rest_framework_services.dispatch.provider_keys import provider_keys
from rest_framework_services.exceptions.service_validation_error import (
    ServiceValidationError,
)
from rest_framework_services.service_specs.invalid_arguments import InvalidArguments
from rest_framework_services.service_specs.kernel_spec_variant import kernel_spec_variant
from rest_framework_services.service_specs.translate_dispatch_errors import (
    translate_dispatch_errors,
)
from rest_framework_services.service_specs.utils import (
    EDGE_CALL,
    SERIALIZER_CONTEXT_SEED,
    EdgeCallState,
    bind_serializer,
    describe_serializer,
    edge_call_state,
    input_parameters,
)
from rest_framework_services.types.argument_binding import ArgumentBinding
from rest_framework_services.types.dispatch_result import DispatchResult
from rest_framework_services.types.marked_input_keys import marked_input_keys
from rest_framework_services.types.offline_context import OfflineContext
from rest_framework_services.types.offline_service_view import OfflineServiceView
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

# The names the edge registers as seeds on every call. ``serializer`` is not one:
# the Validator's own pool delivers it, and the kernel refuses a Validator that
# binds a name a seed occupies. What the edge carries for its own machinery - a
# selector spec's guard, the author values, the view's serializer context
# layers - is no seed: it rides in ``EdgeCallState``, so a callable taking a bare
# ``**kwargs`` is never handed it.
EDGE_SEEDS: tuple[str, ...] = ("request", "view")
# What the Validator binds for the call. An author's value of that name stays in
# ``extra_kwargs``, the tier beneath it, so the bound serializer is what a
# service declaring ``serializer`` receives, as it always was
# (test_the_bound_serializer_outranks_an_authors_value_of_its_name).
VALIDATOR_BOUND: frozenset[str] = frozenset({"serializer"})


@dataclasses.dataclass(frozen=True)
class EdgeCall:
    """One call of the DRF edge, prepared: ``dispatch(spec, **kwargs)`` runs it.

    ``spec`` is the compiled variant the ``Grant`` in ``kwargs`` was minted for;
    ``kwargs`` is everything else ``dispatch`` and ``adispatch`` take, by name.
    """

    spec: KernelServiceSpec | KernelSelectorSpec
    kwargs: dict[str, Any]


@contextmanager
def edge_call(state: EdgeCallState) -> Iterator[None]:
    """``state`` as the edge call in progress, for the whole of the ``with`` block.

    Both cores enter it before ``prepare_edge_call`` fills ``state`` and leave
    it once the kernel has returned, so every compiled piece that reads it -
    the guarded precondition, the ``kwargs`` seat, the Validator - runs inside
    it, and a nested edge call restores the outer one's on the way out.
    """
    token = EDGE_CALL.set(state)
    try:
        yield
    finally:
        EDGE_CALL.reset(token)


def with_output_layers(result: DispatchResult, state: EdgeCallState) -> DispatchResult:
    """``result`` carrying the view's output serializer context layers, for its presenter.

    The presenter runs when the transport renders, after the call, so the
    view's lazy ``output_serializer_context`` hook travels on the result's
    presentation context, under ``SERIALIZER_CONTEXT_SEED``, rather than
    through the call's pool.
    """
    # A missing row is presented by no one, and the kernel builds it no context
    # (test_a_missing_row_under_output_hooks_is_still_not_found).
    if result.context is None:
        return result
    ambient = MappingProxyType(
        {**result.context.ambient, SERIALIZER_CONTEXT_SEED: state.view_hooks}
    )
    return dataclasses.replace(result, context=dataclasses.replace(result.context, ambient=ambient))


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
    state: EdgeCallState,
) -> EdgeCall | DispatchResult:
    """Everything ``dispatch_spec`` does before the kernel's ``dispatch``, as one sync step.

    Returns the prepared call, or the kernel's not-found ``DispatchResult`` when
    a service spec's target lookup found no row. Runs the guard, so it is the
    step an async caller hops to a thread for, once. Raise the kernel's own
    errors; run it inside ``edge_errors``, and inside ``edge_call(state)``,
    since what it carries for the call's machinery it writes to ``state``.
    """
    refuse_retired_modes(argument_binding, unknown_arguments)
    if not isinstance(spec, ServiceSpec | SelectorSpec):
        raise TypeError(
            f"dispatch_spec expects a ServiceSpec or SelectorSpec; got {type(spec).__name__}."
        )
    hooks = view_hooks if view_hooks is not None else ViewHooks()
    state.view_hooks = hooks
    # One object for the whole call: a ``Grant`` binds its principal by identity.
    principal = user if user is not None else unauthenticated_user()
    shared: dict[str, Any] = {
        "principal": principal,
        "unknown_arguments": enforceable_policy(spec, unknown_arguments, argument_binding),
        "progress": (
            progress if hooks.progress is None else combine_progress(progress, hooks.progress)
        ),
    }
    guard_context = OfflineContext(user=user, request=request, view=view)
    if isinstance(spec, SelectorSpec):
        variant: Any = kernel_spec_variant(spec, unguarded=True, server_input=False)
        seeds = edge_seeds(pool_seeds, request=request, view=view)
        if on_target_resolved is not None:
            variant = guarded_selector(variant)
            guard = on_target_resolved

            def bound_guard(target: Any) -> None:
                guard(spec, guard_context, instance=target)

            state.target_guard = bound_guard
        arguments = without_seed_names(
            edge_arguments(variant, spec, params, request=request, view=view), pool_seeds
        )
        _route_filter_data(arguments, variant.filter_set, filter_data)
        return EdgeCall(
            variant,
            {
                **shared,
                "arguments": arguments,
                "extra_kwargs": {
                    **_hidden_captures(spec, view, seeds=seeds),
                    **_route_author_values(
                        spec, variant, arguments, hooks, seeds=seeds, view=view, request=request
                    ),
                },
                "grant": Grant(variant, principal),
                "pool_seeds": seeds,
            },
        )
    if spec.many:
        variant = cast(
            KernelServiceSpec,
            kernel_spec_variant(
                spec, partial=partial, unguarded=True, server_input=False, lenient=over_http(view)
            ),
        )
        seeds = edge_seeds(pool_seeds, request=request, view=view)
        rows = params
        if many_as_argument:
            arguments = edge_arguments(variant, spec, params, request=request, view=view)
        else:
            arguments = edge_arguments(variant, spec, {}, request=request, view=view)
            arguments[spec.many_argument] = rows
        arguments = without_seed_names(arguments, pool_seeds)
        # Each row as the top level is. One arc to coverage, so each conjunct is
        # named: test_arguments_that_are_not_an_object_are_refused fails without
        # the first (a list sent as the arguments), and
        # test_a_non_list_is_refused_under_the_argument without the second (a
        # string would be read as rows); test_a_seed_name_inside_a_row_is_not_unexpected
        # holds the drop itself.
        sent_rows: list[Any] | None = None
        if isinstance(arguments, dict) and isinstance(arguments.get(spec.many_argument), list):
            sent_rows = arguments[spec.many_argument]
            arguments[spec.many_argument] = [
                without_seed_names(row, pool_seeds) for row in sent_rows
            ]
        # A bulk call acts on no row, so the provider is handed none.
        server = server_input(spec, hooks, view=view, request=request, instance=None)
        if server and isinstance(arguments.get(spec.many_argument), list):
            arguments[spec.many_argument] = [
                {**row, **server} if isinstance(row, Mapping) else row
                for row in arguments[spec.many_argument]
            ]
        # The rows the serializer binds are the ones sent, the server's keys over
        # each, as ``input_data`` always merged per row: a seed's name in a row
        # is the serializer's own field
        # (test_a_writable_field_named_user_validates_in_each_row).
        if sent_rows is not None:
            state.validator_input = (
                variant.validator,
                [{**row, **server} if isinstance(row, Mapping) else row for row in sent_rows],
            )
        extra_kwargs = _route_author_values(
            spec, variant, arguments, hooks, seeds=seeds, view=view, request=request
        )
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
    # A drfs ``ServiceSpec`` compiles to the kernel's. Its own parameters are the
    # caller's only under an explicit spreading binding: ``AUTO`` is ``BUNDLE``
    # for a service, which hands the caller's values to what reads them and to
    # nothing else (test_a_client_value_never_fills_a_service_parameter_off_http).
    spread = resolve_argument_binding(spec, argument_binding) is ArgumentBinding.SPREAD_AUTHOR_WINS
    seeds = edge_seeds(pool_seeds, request=request, view=view)
    # The input serializer's fields a seed of the call takes, its own or a route
    # capture the target lookup hides (``_seeded_fields``).
    excluded = _seeded_fields(spec, seeds.names | hidden_lookup_captures(spec, view, seeds=seeds))
    service = cast(
        KernelServiceSpec,
        kernel_spec_variant(
            spec,
            partial=partial,
            unguarded=True,
            target=handed,
            server_input=False,
            spread=spread,
            lenient=over_http(view),
            excluded=excluded,
        ),
    )
    arguments = without_seed_names(
        edge_arguments(service, spec, params, request=request, view=view), pool_seeds
    )
    # A field named like a seed of the call is the serializer's alone, read from
    # the body: never an argument a strict caller is refused for
    # (test_a_field_named_like_a_captured_hidden_key_is_no_unexpected_argument).
    # Arguments that are no object are left for the shape check to refuse
    # (test_arguments_that_are_no_object_beside_a_seed_named_field_are_refused).
    if isinstance(arguments, dict):
        for name in excluded:
            arguments.pop(name, None)
    # What the caller's body became before the edge put anything over it, so
    # the body the Validator binds takes exactly what the edge put there.
    sent = dict(arguments) if isinstance(arguments, dict) else None
    target_spec = service.target_selector_spec()
    if target_spec is not None:
        _route_filter_data(arguments, target_spec.filter_set, filter_data)
    # Before the lookup, which an author's value for one of its reads scopes.
    extra_kwargs = _route_author_values(
        spec, service, arguments, hooks, seeds=seeds, view=view, request=request
    )
    seeds = _hidden_lookup_seeds(spec, service, view, seeds=seeds, extra_kwargs=extra_kwargs)
    state.author_values, extra_kwargs = _seat_author_values(extra_kwargs)
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
        # Only where a lookup will query: a create resolves nothing, and its
        # refusal stays whole, as a ``many`` call's is. One arc to coverage:
        # test_an_open_target_lookup_takes_an_undeclared_key_and_drops_it holds
        # the policy (what ``enforceable_policy`` relaxed refuses nothing), and
        # the missing-and-unexpected case of test_the_refusal_is_the_workarounds
        # holds the lookup.
        if shared["unknown_arguments"] is UnknownArguments.REJECT and target_spec is not None:
            refuse_unknown_first(service, principal=principal, arguments=arguments, seeds=seeds)
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
    # After the lookup, as on main: the provider is handed the row it found.
    server = server_input(spec, hooks, view=view, request=request, instance=target)
    if server and isinstance(arguments, dict):
        arguments.update(server)
    # One arc to coverage, so each conjunct is named: a spec with no input
    # serializer has no Validator (test_the_return_is_passed_through_as_it_came),
    # and a body that is no object has no fields to bind
    # (test_a_single_call_body_that_is_no_object_is_refused_in_drfs_words).
    if service.validator is not None and sent is not None:
        state.validator_input = (
            service.validator,
            _validator_body(params, sent, arguments, service.validator.parameters().names()),
        )
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


def over_http(view: Any) -> bool:
    """Whether the call is a request a live DRF view dispatches.

    Such a request is validated by its serializer alone, as DRF's views always
    validated one (``kernel_spec_variant(lenient=True)``), and refused in
    DRF's words (``edge_errors``). Read off the view without importing DRF's
    views, which the dispatch core does not: a call with no view, or with the
    ``OfflineServiceView`` an agent transport builds, keeps the kernel's typed
    check (each case of test_an_offline_view_keeps_the_kernels_typed_check
    holds one of the two conditions), and any other view is a transport's
    own, as a DRF view is.
    """
    return view is not None and not isinstance(view, OfflineServiceView)


def _validator_body(
    params: Mapping[str, Any],
    sent: Mapping[str, Any],
    arguments: Mapping[str, Any],
    fields: frozenset[str],
) -> Any:
    """The body a service spec's Validator binds: the caller's ``params``, with the edge's keys.

    DRF's serializer was always handed the request's data as the request parsed
    it, so a ``QueryDict`` stays one and is read as HTML input: a checkbox's
    ``"on"``, a form's empty string for an optional field, an unchecked box's
    absence as ``False`` (test_a_form_body_is_read_as_html_input). The kernel's
    arguments are a flat copy, so what the edge put over the caller's values
    for a Validator field - the server's ``input_data``, an author's value - is
    written onto the body the way ``input_data`` always was, a list through
    ``setlist`` (test_input_data_reaches_a_form_body_as_a_list), and a value the
    edge took away, which a typed provider declined, is taken from it too
    (test_a_declined_owned_field_is_taken_from_the_body). Nothing put over it
    hands ``params`` on as it is. A seed's name the edge dropped from the
    arguments stays in the body, where the serializer reads its own field of
    that name.
    """
    # A key the edge added (test_the_specs_value_wins_over_the_view_hooks, an
    # ``input_data`` key the caller never sent) or replaced
    # (test_a_hook_value_naming_a_serializer_field_is_the_fields_value).
    put = {
        name: arguments[name]
        for name in fields
        if name in arguments and (name not in sent or arguments[name] is not sent[name])
    }
    taken = [name for name in fields if name in sent and name not in arguments]
    if not put and not taken:
        return params
    if isinstance(params, QueryDict):
        body = params.copy()
        for name in taken:
            body.pop(name, None)
        for name, value in put.items():
            if isinstance(value, list | tuple):
                body.setlist(name, list(value))
            else:
                body[name] = value
        return body
    return {**{name: value for name, value in params.items() if name not in taken}, **put}


def _seeded_fields(spec: ServiceSpec[Any, Any, Any], seeded: frozenset[str]) -> frozenset[str]:
    """The input serializer's fields named in ``seeded``: what a seed the call registers takes.

    A seed the edge registers - ``request``, ``view``, a project's, or a route
    capture a target lookup hides (``hidden_lookup_captures``,
    test_a_route_capture_scopes_the_lookup_beside_a_field_of_its_name) - takes
    its name in the call's pool.

    Compiled as ``kernel_spec_variant(excluded=)``, so each is the serializer's
    field alone, validated from the body into ``data``, while the seed keeps
    its own value, as on main
    (test_a_writable_field_named_like_a_registered_seed_validates). The kernel
    refuses a parameter a registered seed occupies; the dispatcher's own names
    the Validator never declares (``SerializerValidator.parameters``).
    """
    if spec.input_serializer is None:
        return frozenset()
    fields = input_parameters(describe_serializer(spec.input_serializer)).names()
    return frozenset(fields & seeded)


def server_input(
    spec: ServiceSpec[Any, Any, Any],
    hooks: ViewHooks,
    *,
    view: Any,
    request: Any,
    instance: Any,
) -> dict[str, Any]:
    """The server's keys for the Validator's input: the view's ``input_data``, then the spec's.

    The spec's ``input_data`` provider is called here, from ``view``, ``request``
    and ``instance`` (the row the call acts on, ``None`` for a create or a
    ``many`` call), and wins over the view hook's value on a shared key, as it
    did on main. The edge merges the answer over the caller's arguments, so a
    server key always beats the client's. It is the edge's rather than the
    kernel's because the kernel relaxes a Validator field only for a provider
    annotated to fill it, so the untyped provider drfs has always taken would
    meet a missing-field refusal before it ran
    (test_input_data_applies_off_http); the dispatched variant carries none, so
    the provider runs once (test_the_input_data_provider_runs_once_per_call). A
    key the provider answers ``UNSET`` is declined, as main read it.
    """
    merged: dict[str, Any] = dict(hooks.input_data or {})
    provider = spec.input_data
    if provider is not None:
        pool: dict[str, Any] = {"view": view, "request": request, "instance": instance}
        answered = provider(**resolve_callable_kwargs(provider, pool))
        merged.update({key: value for key, value in answered.items() if value is not UNSET})
    return merged


def listed_output(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any], result: DispatchResult
) -> DispatchResult:
    """``result`` as a list where ``spec`` declares a ``LIST`` output it does not re-read.

    ``output_selector_spec=SelectorSpec(kind=LIST)`` with no ``selector``
    compiles to no output selector, so the kernel presents the service's own
    return and reports it as one value. The declaration says that return is the
    set of rows, so the edge reports ``kind="list"`` for it and the transport
    presents it per row, once ``service_return_as_list`` has found it is one.
    A not-found result, and every other spec, comes back as it was. Both cores
    call it after the kernel returns, and it runs no query.
    """
    if not isinstance(spec, ServiceSpec):
        return result
    output = spec.output_selector_spec
    # One arc to coverage, so each condition is named by the test that fails
    # without it: test_a_retrieve_declaration_with_no_re_read_is_still_one_value
    # (the declared kind) and
    # test_a_missing_row_under_a_list_declaration_stays_not_found (the result's).
    # A ``LIST`` output that re-reads needs no condition of its own: the kernel
    # already answers it ``"list"``, so the second condition returns it as is.
    if output is None or output.kind is not SelectorKind.LIST or result.kind != "instance":
        return result
    return dataclasses.replace(
        result, kind="list", value=service_return_as_list(spec, result.value)
    )


def service_return_as_list(spec: ServiceSpec[Any, Any, Any], value: Any) -> Any:
    """``value``, once it is a set of rows a ``LIST`` declaration can present.

    The declaration says the service returns the set, so a return that cannot be
    one is the author's error: a mapping, a ``str`` or ``bytes`` (iterable, but by
    key or character rather than by row), anything else that does not iterate, and
    ``None``, since a list is empty rather than absent. It is raised as
    ``ImproperlyConfigured``, not a refusal: the service has run and its write
    stands, so it must not reach a caller as a validation error saying nothing
    happened.

    The test is one arc to coverage, so each member is a row of
    ``test_a_return_that_is_not_a_set_of_rows_is_the_authors_error``, which fails
    without it: ``mapping``, ``str``, ``bytes``, ``non-iterable`` and ``none``.
    """
    if isinstance(value, Mapping | str | bytes) or not isinstance(value, Iterable):
        label = getattr(spec.service, "__qualname__", repr(spec.service))
        raise ImproperlyConfigured(
            "output_selector_spec declares kind=LIST with no selector, so the service's "
            f"own return is the list presented, and {label} returned "
            f"{type(value).__name__}, which is neither a QuerySet nor an iterable of "
            "rows. Return the rows, or declare kind=SelectorKind.RETRIEVE to present one "
            "value. The service has already run, so its write stands."
        )
    return value


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
    still loading, and this package is imported then. Where a project sets
    ``UNAUTHENTICATED_USER`` to ``None``, DRF's own ``Request`` carries ``None``,
    which the kernel takes as no principal at all (``adispatch`` refuses it with
    ``TypeError``), so the edge acts as Django's ``AnonymousUser`` there, imported
    by name at call time for the same reason
    (test_a_project_with_no_unauthenticated_user_still_acts_as_anonymous).
    """
    # A class, or ``None``; DRF's settings object is untyped.
    factory: Any = api_settings.UNAUTHENTICATED_USER
    if factory is None:
        factory = import_string("django.contrib.auth.models.AnonymousUser")
    return factory()


def edge_seeds(pool_seeds: PoolSeeds, *, request: Any, view: Any) -> PoolSeeds:
    """The caller's seeds, behind this call's ``request`` and ``view``.

    The kernel resolves each seed against the base pool alone, so a resolver of
    the caller's that declares ``request`` or ``view`` - as this package's seeds
    always could - is handed them by a wrapper, which stays sync.

    Raises:
        ImproperlyConfigured: ``pool_seeds`` registers ``request`` or ``view``,
            which this edge seeds itself from what it was handed.
    """
    clash = sorted(pool_seeds.names & set(EDGE_SEEDS))
    if clash:
        raise ImproperlyConfigured(
            f"pool_seeds registers {', '.join(repr(name) for name in clash)}, which "
            "dispatch_spec seeds itself from the request and view it is handed. "
            "Pass them as request= and view=, and remove the registration."
        )
    ambient = {"request": request, "view": view}
    wrapped = tuple((name, _with_ambient(resolver, ambient)) for name, resolver in pool_seeds.seeds)
    return PoolSeeds(seeds=(("request", lambda: request), ("view", lambda: view), *wrapped))


def _with_ambient(resolver: Callable[..., Any], ambient: Mapping[str, Any]) -> Callable[..., Any]:
    def resolve(**pool: Any) -> Any:
        return resolver(**resolve_callable_kwargs(resolver, {**pool, **ambient}))

    return resolve


def without_seed_names(arguments: Any, pool_seeds: PoolSeeds) -> Any:
    """``arguments`` less every key naming a seed: a reserved one or one the caller registered.

    A seed's name is never the caller's to send, and this package never counted
    one as unknown: ``UnknownArguments.REJECT`` exempted them, so a strict caller
    is not refused for a ``user`` key the call would have ignored. The kernel
    refuses every key it does not declare, a seed's name among them, so the edge
    drops them first (test_a_built_in_seed_name_is_not_unexpected,
    test_a_registered_seed_name_is_not_an_unexpected_argument). ``view``, which
    only the edge seeds, is not dropped: a caller sending it was refused under
    ``REJECT`` before the edge seeded it, and still is
    (test_the_view_is_unexpected_though_the_edge_seeds_it). Arguments that are no
    mapping are handed on as they are, for the kernel's shape check to refuse.
    """
    if not isinstance(arguments, dict):
        return arguments
    reserved = pool_seeds.reserved
    return {name: value for name, value in arguments.items() if name not in reserved}


def enforceable_policy(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any],
    unknown_arguments: UnknownArguments,
    argument_binding: ArgumentBinding,
) -> UnknownArguments:
    """``unknown_arguments`` as the kernel can hold ``spec``'s caller to it.

    The kernel closes the argument set on what the spec declares. A callable
    taking a bare ``**kwargs`` that reads the caller's input declares no set,
    and this package never refused a key such a callable could be handed, so
    ``REJECT`` there is answered as ``IGNORE``, which drops an undeclared key
    rather than refuse it. The callables, read in this package's order:

    - a selector, or a service's target lookup
      (test_an_open_selector_takes_an_undeclared_key_and_drops_it,
      test_an_open_target_lookup_takes_an_undeclared_key_and_drops_it); when
      it is open, nothing else is read;
    - a service with no ``input_serializer`` dispatched under a spreading
      ``argument_binding``, whose parameters are then its input
      (test_an_open_spreading_service_takes_an_undeclared_key_and_drops_it).
      ``_spread_parameter_keys`` names the test holding each of its
      conditions.

    No policy hands the key on: ``PASSTHROUGH`` is retired.

    Raises:
        ImproperlyConfigured: ``REJECT`` on a callable whose ``**kwargs``
            annotation does not resolve at runtime: the surface is unknown, not
            open, and a strict policy cannot be honoured against it.
    """
    if unknown_arguments is not UnknownArguments.REJECT:
        return unknown_arguments
    try:
        is_open = _reads_openly(spec, argument_binding)
    except _UnresolvedExtras as exc:
        raise ImproperlyConfigured(
            f"UnknownArguments.REJECT cannot be enforced: {exc} Make the annotation "
            "resolvable at runtime (import the TypedDict normally rather than under "
            "'if TYPE_CHECKING:'), or dispatch with UnknownArguments.IGNORE."
        ) from exc
    return UnknownArguments.IGNORE if is_open else unknown_arguments


def _reads_openly(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any], argument_binding: ArgumentBinding
) -> bool:
    """Whether a callable reading ``spec``'s caller's input takes a bare ``**kwargs``.

    Two arcs to coverage, so each condition is named by the test that fails
    without it: no reader (test_an_open_spreading_service_takes_an_undeclared_key_and_drops_it),
    a closed one (test_a_closed_lookup_still_refuses_an_undeclared_key), and a
    selector spec, which spreads no service
    (test_a_registered_seed_name_is_not_an_unexpected_argument).
    """
    lookup = _target_lookup(spec) if isinstance(spec, ServiceSpec) else spec
    reader = lookup.selector if lookup is not None else None
    if reader is not None and _callable_param_names(reader) is None:
        return True
    return (
        isinstance(spec, ServiceSpec)
        and _spread_parameter_keys(
            spec, argument_binding=argument_binding, reserved=RESERVED_POOL_SEEDS
        )
        is None
    )


def refuse_unknown_first(
    service: KernelServiceSpec, *, principal: Any, arguments: Any, seeds: PoolSeeds
) -> None:
    """Refuse the caller's undeclared keys under ``REJECT``, before the target lookup queries.

    ``UnknownArguments.REJECT`` always answered an undeclared key first, so a
    call naming one is a 400 whether or not its row exists, and the database is
    not asked for the row
    (test_an_unknown_key_is_refused_before_the_lookup_asks_the_database).
    ``resolve_target`` checks only the lookup's own reads, and ``dispatch``
    checks the rest after it, so the kernel's ``check_call`` runs here, which
    is ``dispatch``'s own prelude and runs no Validator: every ``"unknown"`` it
    raises is the kernel's. Only those are raised, alone, as they always were
    (test_an_unknown_key_ahead_of_the_lookup_is_refused_alone); any other
    refusal is left for ``dispatch`` to make once there is a row, so a missing
    row with a field left out is still ``not_found``
    (test_a_missing_row_and_a_missing_field_is_still_not_found).
    """
    try:
        check_call(
            service,
            principal=principal,
            arguments=arguments,
            grant=Grant(service, principal),
            pool_seeds=seeds,
            unknown_arguments=UnknownArguments.REJECT,
        )
    except KernelInvalidArguments as exc:
        unknown = {name: errors for name, errors in exc.detail.items() if _only_unknown(errors)}
        if unknown:
            raise KernelInvalidArguments(unknown) from exc


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
    may capture; a declared ``UrlKwarg`` the caller sent and an offline view
    does not carry goes onto ``view.kwargs``, so a provider reading it there
    finds it. A live DRF view's route is its URLconf's alone (``over_http``).
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
        # A live view's ``kwargs`` are its URLconf's, which a permission class
        # reads as the author's, so a caller's value is never written there
        # (test_a_body_value_never_reaches_a_live_views_kwargs).
        if not over_http(view):
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


def _route_author_values(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any],
    variant: KernelServiceSpec | KernelSelectorSpec,
    arguments: Any,
    hooks: ViewHooks,
    *,
    seeds: PoolSeeds,
    view: Any,
    request: Any,
) -> dict[str, Any]:
    """The author's values for the call: the view's hook extras, then the spec's ``kwargs`` provider.

    Both are the server's, so both outrank the caller, and the spec's provider
    outranks the view's hooks, as the chain always ran ``get_*_kwargs``, then
    ``get_<action>_*_kwargs``, then ``spec.kwargs``. Each is split by
    ``_split_author_values``: a value for a name the caller fills goes into
    ``arguments``, over the caller's, and is checked there as the caller's
    would be; the rest is returned for ``dispatch(extra_kwargs=)``.

    The spec's provider is called here, from ``view`` and ``request``, once per
    call (test_a_provider_runs_once_per_call), and the dispatched variant
    carries none (``kernel_spec_variant(server_input=False)``). The kernel
    relaxes a parameter only for a provider annotated to fill it, so the
    untyped provider drfs has always taken would meet a missing-argument
    refusal before it ran (test_a_provider_satisfies_the_requirement), and a
    typed one declining a key it is annotated to fill would reach the run as a
    ``KeyError`` (test_a_typed_provider_declining_a_required_key_is_refused_as_missing).
    A key either answers ``UNSET`` is declined, as main read it, so the
    caller's value stands.

    A hook's value naming a seed of the call - ``request``, ``view``,
    ``progress``, ``user`` and the rest of the reserved names, or a project's
    registered seed - is dropped, so the seed wins: a view's hook was never the
    place to replace one
    (test_a_hook_value_naming_a_seed_is_dropped_and_the_seed_wins). The spec's
    provider naming one is refused, as the kernel refuses it, so the bug is
    found (test_a_provider_handing_over_the_view_is_refused).
    """
    seeded = seeds.reserved
    provider = spec.kwargs
    keys = provider_keys(provider)
    owned = keys.filled if keys is not None else frozenset()
    hook_values = {
        name: value
        for name, value in (hooks.extra_kwargs or {}).items()
        if name not in seeded and name not in owned
    }
    if isinstance(arguments, dict):
        for name in owned:
            arguments.pop(name, None)
    provided: dict[str, Any] = {}
    if provider is not None:
        answered = provider(**resolve_callable_kwargs(provider, {"view": view, "request": request}))
        taken = sorted(answered.keys() & seeded)
        if taken:
            label = f"{type(spec).__name__}.kwargs"
            raise ImproperlyConfigured(
                f"{label} returned the key(s) {taken}, which dispatch seeds itself. A "
                "provider's value must never outrank a seeded one; rename the key."
            )
        provided = dict(answered)
    wire = _source_to_wire(spec)
    return {
        **_split_author_values(variant, arguments, hook_values, wire=wire),
        **_split_author_values(variant, arguments, provided, wire=wire),
    }


def _source_to_wire(spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any]) -> dict[str, str]:
    """``{source: wire_name}`` for the top-level fields of a single call's input serializer.

    A view's hooks and the spec's provider have always named what the service
    is handed, which is a field's ``source`` where it has one, so a value for
    ``tenant_id`` is the value of the field the request calls ``tenant``
    (test_a_hook_value_naming_a_fields_source_is_the_fields_value). A ``many``
    spec's fields are each row's, and are no argument of the call, so a value
    is never renamed to one of their wire names
    (test_a_many_specs_hook_values_are_named_as_the_service_is_handed_them).
    """
    if not isinstance(spec, ServiceSpec) or spec.input_serializer is None or spec.many:
        return {}
    described = _wire_names(describe_serializer(spec.input_serializer))
    return {source: wire_name for source, (wire_name, _) in described.items()}


def _split_author_values(
    variant: KernelServiceSpec | KernelSelectorSpec,
    arguments: Any,
    values: Mapping[str, Any],
    *,
    wire: Mapping[str, str],
) -> dict[str, Any]:
    """Put each of ``values`` naming a caller's argument into ``arguments``; return the rest.

    The names a caller fills at the top level of the call: the reads of the
    compiled spec (its service's or selector's, and a service spec's target
    lookup's) and, on a service spec that is not ``many``, its Validator's
    fields. The kernel refuses ``extra_kwargs`` naming any of them, and the
    author's value for one is the server's, so it is put over the caller's and
    validated as the caller's would be: a view's ``get_service_kwargs()``
    returning the serializer's ``tenant`` decides the tenant, whatever the body
    said (test_service_kwargs_hook_applies_to_single). A ``many`` spec's
    Validator fields are each row's, so a value naming one stays an extra the
    service is handed beside its rows, as it always was
    (test_service_kwargs_hook_applies_to_bulk); and ``many_argument`` is no
    such name, or a value named like it would replace the rows. Arguments that
    are no mapping take nothing, for the kernel's shape check to refuse.

    One arc to coverage, so each condition that keeps a value an extra is
    named by the test that fails without it: an ``UNSET`` value supplies
    nothing (test_an_unset_hook_value_leaves_the_clients), and a name that is
    no argument stays the overlay's
    (test_a_hook_value_for_a_hidden_name_still_goes_as_an_extra).
    """
    names = set(variant.reads.names())
    if isinstance(variant, KernelServiceSpec):
        target_spec = variant.target_selector_spec()
        if target_spec is not None:
            names |= target_spec.reads.names()
        if variant.validator is not None and not variant.many:
            names |= variant.validator.parameters().names()
    named = {wire.get(name, name) if name not in names else name: name for name in values}
    routed = {
        argument: values[name]
        for argument, name in named.items()
        if values[name] is not UNSET and argument in names
    }
    if isinstance(arguments, dict):
        arguments.update(routed)
    return {
        name: values[name]
        for argument, name in named.items()
        if argument not in routed and values[name] is not UNSET
    }


def _hidden_captures(
    spec: SelectorSpec[Any, Any], view: Any, *, seeds: PoolSeeds
) -> dict[str, Any]:
    """The route's values for the names a selector spec's callables mark ``NotClientInput``.

    A hidden name is no read, so no caller's argument fills it, but a route
    capture of that name always did: the URLconf is the author's. It reaches
    the selector as a call extra, which the kernel drops beneath the spec's
    own provider and which a view hook's value for the name outranks
    (test_a_route_capture_fills_a_selectors_hidden_key). A name a seed
    occupies is the seed's, as ``view`` always is.

    A service spec's target lookup is not reached this way: the kernel hands
    ``extra_kwargs`` to a selector spec's selector and a service, never to a
    target selector, and ``resolve_target`` takes none. ``_hidden_lookup_seeds``
    seeds those instead.
    """
    route = getattr(view, "kwargs", None)
    if not isinstance(route, dict):
        return {}
    hidden = server_owned_keys(spec) - seeds.reserved
    return {name: route[name] for name in sorted(hidden) if name in route}


def _hidden_lookup_seeds(
    spec: ServiceSpec[Any, Any, Any],
    service: KernelServiceSpec,
    view: Any,
    *,
    seeds: PoolSeeds,
    extra_kwargs: dict[str, Any],
) -> PoolSeeds:
    """``seeds``, plus a value for each name a service spec's target lookup hides and the route captures.

    A route capture of a name the lookup marks ``NotClientInput`` always filled
    it - ``/projects/<project_pk>/tasks/<pk>/`` - since the URLconf is the
    author's (test_a_route_capture_still_fills_a_hidden_lookup_key). The
    kernel's ``extra_kwargs`` never reach a target lookup and ``resolve_target``
    takes none, so each such capture is registered as a seed of this call, which
    the lookup is handed by name, one resolver per value
    (test_each_route_capture_fills_its_own_hidden_lookup_key).

    A seed is the one value of its name in the call, and the kernel refuses any
    other source for it, so:

    - an author's value for the name, a view hook's or the spec's provider's,
      is seeded instead of the capture and leaves ``extra_kwargs``: it is what
      the service was handed, and the lookup is handed it too
      (test_an_authors_value_for_a_hidden_lookup_key_is_seeded_over_the_capture);
    - a name the lookup's own provider may answer is not seeded, so its
      provider fills it (test_a_lookup_provider_answering_a_hidden_key_wins);
      an untyped one may answer any name, so nothing is seeded beside it and
      a capture does not reach that lookup (the ``untyped`` case of
      test_a_lookup_provider_answering_a_hidden_key_wins);
    - a name the call already seeds is the seed's, as ``view`` always is
      (test_a_registered_seed_named_like_a_hidden_lookup_key_wins_over_the_capture);
    - a name the spec declares as a parameter - one of its reads - is the
      caller's argument
      (test_a_read_named_like_a_hidden_lookup_key_is_the_service_s_argument). An input serializer's field of the same name is not such a
      parameter: the edge compiles the Validator without it
      (``hidden_lookup_captures``), so the capture scopes the lookup and the
      field is the client's, validated from the body into ``data``, as on main
      (test_a_route_capture_scopes_the_lookup_beside_a_field_of_its_name).

    A capture the lookup does not hide is no argument, as before.
    """
    hidden = hidden_lookup_captures(spec, view, seeds=seeds) - service.parameters().names()
    if not hidden:
        return seeds
    # A name is captured only off a route the view carries.
    route = view.kwargs
    return seeds.extend(
        **{
            name: _constant(extra_kwargs.pop(name) if name in extra_kwargs else route[name])
            for name in sorted(hidden)
        }
    )


def hidden_lookup_captures(
    spec: ServiceSpec[Any, Any, Any], view: Any, *, seeds: PoolSeeds
) -> frozenset[str]:
    """The names a service spec's target lookup hides that the route captures and nothing else fills.

    What ``_hidden_lookup_seeds`` seeds, before it sets aside a declared
    parameter: not a name the call already seeds, nor one the lookup's own
    provider may answer.

    One arc to coverage, so each condition that captures nothing is named by
    the test that fails without it: no route to read
    (test_with_no_view_a_hidden_lookup_key_keeps_its_default), no lookup
    (test_an_offline_requests_query_string_is_not_read_either), and a lookup
    with no selector
    (test_selectorless_instance_spec_falls_back_to_get_object).
    """
    route = getattr(view, "kwargs", None)
    lookup = _target_lookup(spec)
    if not isinstance(route, dict) or lookup is None or lookup.selector is None:
        return frozenset()
    keys = provider_keys(lookup.kwargs)
    if keys is None:
        return frozenset()
    hidden = hidden_input_keys(lookup.selector) - seeds.reserved - keys.filled - keys.declinable
    return frozenset(name for name in hidden if name in route)


def _seat_author_values(extra_kwargs: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """A single service call's author values, and what stays an overlay.

    The author's values that name no argument - a view hook's, the spec's
    ``kwargs`` provider's - have always outranked whatever the client's input
    became: a key the input serializer's ``validate()`` derives, or a
    ``source="*"`` nested field flattened into ``validated_data``. The kernel
    hands ``extra_kwargs`` beneath every key the Validator returns, so they are
    carried in the call's ``EdgeCallState`` instead, which the compiled spec's
    ``kwargs`` seat hands back over the Validator's values
    (test_a_hooks_value_outranks_the_validators_off_http,
    test_the_specs_provider_outranks_the_validators_off_http). The client's
    value stays in ``data``, where it was validated. What the Validator binds
    stays an overlay beneath it (``VALIDATOR_BOUND``): the bound serializer
    wins where there is one
    (test_the_bound_serializer_outranks_an_authors_value_of_its_name), and the
    author's value reaches a service no Validator binds one for
    (test_an_authors_serializer_value_reaches_a_service_no_validator_binds_one_for).

    A ``many`` spec's values are its rows alone, and a selector spec has no
    Validator, so neither is seated: both keep their ``extra_kwargs``.
    """
    seated = {name: value for name, value in extra_kwargs.items() if name not in VALIDATOR_BOUND}
    overlay = {name: value for name, value in extra_kwargs.items() if name in VALIDATOR_BOUND}
    return seated, overlay


def _constant(value: Any) -> Callable[[], Any]:
    # One resolver per value: a lambda in the loop above would bind the last.
    def resolve() -> Any:
        return value

    return resolve


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
    # test_no_filter_data_leaves_the_arguments_to_filter fails without the
    # first, and test_arguments_that_are_no_object_take_no_filter_data without
    # the second. No ``filter_set`` reads nothing: ``filter_set_parameters``
    # answers ``None`` with no Parameters, so its names are none to route.
    if filter_data is None or not isinstance(arguments, dict):
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
    call's, read from its ``EdgeCallState``.
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


def _guard_row(*, instance: Any) -> None:
    _call_guard(instance)


def _guard_set(*, collection: Any) -> None:
    _call_guard(collection)


def _call_guard(target: Any) -> None:
    """Run the guard of the edge call in progress on ``target``, refusing a call that carries none.

    Only the edge dispatches a guarded twin, and always with its guard, so a
    twin reached any other way fails closed rather than skipping the check
    (test_a_guarded_twin_dispatched_without_its_guard_is_refused).
    """
    guard = edge_call_state().target_guard
    if guard is None:
        raise ImproperlyConfigured(
            "A guarded selector spec was dispatched outside the call that guards it. "
            "Dispatch the selector spec through dispatch_spec with on_target_resolved=."
        )
    guard(target)


@contextmanager
def edge_errors(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any],
    params: Any,
    *,
    many_as_argument: bool,
    http: bool,
) -> Iterator[None]:
    """The kernel's refusals as DRF's, around a whole edge call.

    - Every kernel ``DispatchError`` leaves as its dual-base twin, through
      ``translate_dispatch_errors``.
    - A ``many`` spec whose rows arrived as ``params`` itself - an HTTP list
      body - is refused as DRF's list serializer refuses one: an entry per row,
      ``{}`` for a valid one, and a refusal of the list itself under
      ``non_field_errors``. The kernel keys both under ``many_argument``, which
      such a caller never sent.
    - The kernel's refusal of an undeclared key, ``"Unknown argument."`` under
      the key, is answered as this package always answered
      ``UnknownArguments.REJECT``: one ``"Unexpected argument(s): ..."`` message
      under ``non_field_errors``, at the top level and in each row, where the
      kernel's refusal can be (``_unexpected_as_drf``).
      Here rather than in ``translate_dispatch_errors``, which every transport
      shares: the wording is this edge's answer.
    - A validation error a service or a precondition raises is keyed by the
      names the request used, where the input serializer's ``source=`` makes
      them differ from the ones the service saw. Never the kernel's own
      ``InvalidArguments``, which is already keyed by them.
    - On ``http``, a request a live DRF view dispatches (``over_http``), a
      body that is not what the input serializer takes - no object for a
      single call, no list for a ``many`` one - is refused as the serializer
      refuses it, in DRF's words (``_drf_body_refusal``).
    """
    bare_rows = isinstance(spec, ServiceSpec) and spec.many and not many_as_argument
    try:
        with translate_dispatch_errors():
            yield
    except InvalidArguments as exc:
        drf = _drf_body_refusal(spec, params, bare_rows=bare_rows) if http else None
        if drf is not None:
            raise drf from exc
        detail = _unexpected_as_drf(exc.detail, spec)
        unwrapped = _bare_rows_error(detail, spec.many_argument, params) if bare_rows else None
        if unwrapped is not None:
            raise unwrapped from exc
        # Nothing reshaped leaves as translated, raised from the kernel's own
        # refusal (test_a_refusal_with_nothing_to_reshape_leaves_as_translated).
        if detail == exc.detail:
            raise
        raise InvalidArguments(detail) from exc
    except (ServiceValidationError, ValidationError) as exc:
        serializer = getattr(spec, "input_serializer", None)
        if serializer is None:
            raise
        raise wire_named_error(exc, describe_serializer(serializer)) from exc


def _drf_body_refusal(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any], params: Any, *, bare_rows: bool
) -> ValidationError | None:
    """DRF's own refusal of a body its input serializer cannot take, or ``None``.

    The kernel refuses a body of the wrong shape before any Validator runs, as
    ``"Expected an object."`` or ``"Expected an array."``; a DRF view's
    serializer refused it as ``'Invalid data. Expected a dictionary, but got
    list.'`` (test_a_single_call_body_that_is_no_object_is_refused_in_drfs_words)
    or ``'Expected a list of items but got type "dict".'``
    (test_a_bulk_body_that_is_no_list_is_refused_in_drfs_words), so the
    serializer is asked. It refuses the shape before it reads a field, so it
    is bound with no context. A spec with no input serializer has no DRF
    answer to give, and a ``many`` spec whose rows sit under an argument is
    no body, so both keep the kernel's.
    """
    # A selector spec declares no input serializer, so the second operand alone
    # answers it the same; the first narrows ``spec`` for ``.many`` below. The
    # second is held by
    # test_a_body_with_no_serializer_to_answer_it_keeps_the_kernels_refusal.
    if not isinstance(spec, ServiceSpec) or spec.input_serializer is None:
        return None
    if bare_rows:
        if isinstance(params, list):
            return None
    elif spec.many or isinstance(params, Mapping):
        return None
    bound = bind_serializer(spec.input_serializer, data=params, many=bare_rows)
    bound.is_valid()
    return ValidationError(bound.errors)


def _unexpected_as_drf(
    detail: Any, spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any]
) -> Any:
    """``detail`` with the kernel's unknown-argument refusals in this package's words.

    The kernel refuses each undeclared key under its own name, ``"Unknown
    argument."`` with code ``unknown``; this package always answered
    ``UnknownArguments.REJECT`` with one message naming them all, under
    ``non_field_errors``, and a client reads that. So the keys refused only as
    unknown become that message, and every other refusal keeps its key
    (test_a_field_refusal_beside_an_unknown_key_keeps_its_own_key).

    Only where the kernel's refusal can be: the top level of the call and each
    row of a ``many`` spec, and there only a key the compiled spec does not
    declare. A Validator's own refusal with that code - under a field it
    declares, under ``non_field_errors``, or in a row - is left as raised
    (test_a_validators_unknown_code_is_left_as_it_raised_it,
    test_a_validators_unknown_code_inside_a_row_is_left_as_it_raised_it).
    """
    return _reword_unknown(
        detail, kernel_spec_variant(spec, unguarded=True, server_input=False).parameters()
    )


def _reword_unknown(detail: Mapping[Any, Any], parameters: Parameters) -> dict[Any, Any]:
    """One level of ``detail``, with its undeclared keys refused only as unknown in one message.

    Then each row of a declared array of objects - a ``many`` spec's rows - is
    one level of its own, against the rows' parameters
    (test_an_undeclared_key_inside_an_item_is_refused_at_its_index). Deeper than
    that is left as raised.

    One arc to coverage, so each condition that rewords a key is named by the
    test that fails without it: a declared key is a Validator's
    (test_a_validators_unknown_code_is_left_as_it_raised_it, its ``field``
    case), ``non_field_errors`` is never a key the kernel refuses as unknown
    here (its ``object`` case), and an undeclared key refused otherwise keeps
    its key (test_a_validators_refusal_under_an_undeclared_key_keeps_its_key).
    The kernel writes every level as a mapping: a refusal of a row that is no
    object goes under ``non_field_errors`` inside it.
    """
    declared = parameters.names()
    unknown = [
        name
        for name, errors in detail.items()
        if name not in declared
        and name != api_settings.NON_FIELD_ERRORS_KEY
        and _only_unknown(errors)
    ]
    reworded: dict[Any, Any] = {}
    for name, errors in detail.items():
        if name in unknown:
            continue
        param = parameters.get(name)
        rows = param.items if param is not None else None
        # One arc to coverage, each condition named: a declared object, which
        # has no rows (test_a_refusal_two_objects_deep_is_left_as_raised), and
        # a list refused whole, which is no mapping of rows
        # (test_a_non_list_is_refused_under_the_argument).
        if isinstance(rows, Parameters) and isinstance(errors, dict):
            errors = {index: _reword_unknown(row, rows) for index, row in errors.items()}
        reworded[name] = errors
    if unknown:
        names = ", ".join(repr(name) for name in sorted(str(name) for name in unknown))
        reworded[api_settings.NON_FIELD_ERRORS_KEY] = [
            *reworded.get(api_settings.NON_FIELD_ERRORS_KEY, []),
            ErrorDetail(f"Unexpected argument(s): {names}.", code="invalid"),
        ]
    return reworded


def _only_unknown(errors: Any) -> bool:
    # A row's refusals are a mapping keyed by ``int``, whose keys carry no code,
    # so it is never read as one; the kernel writes no empty refusal.
    return all(getattr(error, "code", None) == "unknown" for error in errors)


def _bare_rows_error(detail: Any, many_argument: str, params: Any) -> ValidationError | None:
    """``detail`` in the shape a list body is refused in, or ``None`` to leave it keyed.

    A refusal of anything beside the rows - a declared read the route left out -
    names something the caller did send, so it keeps its key.
    """
    if set(detail) != {many_argument}:
        return None
    rows = detail[many_argument]
    if isinstance(rows, dict):
        # Rows are refused by index only once the body is a list of them.
        return ValidationError([rows.get(index, {}) for index in range(len(params))])
    return ValidationError({api_settings.NON_FIELD_ERRORS_KEY: rows})


# The calling view. The edge seeds it on every call (``edge_seeds``), so a
# callable declaring ``view`` receives the transport's, or ``None`` off HTTP, and
# a provider or route capture of that name never outranks it. The input schema
# hides it from a selector's parameters, as it hides ``request`` and ``user``, so a
# caller is never told it may send one.
#
# It is not in ``RESERVED_POOL_SEEDS``, deliberately: that set is public, read by
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
