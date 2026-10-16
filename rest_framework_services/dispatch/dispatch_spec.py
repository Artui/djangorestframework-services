"""``dispatch_spec`` - a drfs spec run through the kernel's ``dispatch``, at the DRF edge."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django_service_specs.dispatch.dispatch import dispatch

from rest_framework_services.dispatch.utils import (
    edge_call,
    edge_errors,
    listed_output,
    over_http,
    prepare_edge_call,
    with_output_layers,
)
from rest_framework_services.service_specs.register_drf_row_errors import (
    register_drf_row_errors,
)
from rest_framework_services.service_specs.utils import EdgeCallState
from rest_framework_services.types.argument_binding import ArgumentBinding
from rest_framework_services.types.dispatch_result import DispatchResult
from rest_framework_services.types.pool_seeds import DEFAULT_POOL_SEEDS, PoolSeeds
from rest_framework_services.types.progress_reporter import ProgressReporter
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.types.target_guard import TargetGuard
from rest_framework_services.types.unknown_arguments import UnknownArguments
from rest_framework_services.types.unset import UNSET
from rest_framework_services.types.view_hooks import ViewHooks

# This package has no ``AppConfig`` to register from, and the edge is where a
# nested write's DRF row refusals first need the kernel to know them. Idempotent.
register_drf_row_errors()


def dispatch_spec(
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
    partial: bool | None = None,
) -> DispatchResult:
    """Run ``spec`` through the kernel's ``dispatch``, as DRF would, returning its
    [`DispatchResult`][rest_framework_services.types.dispatch_result.DispatchResult].

    The edge compiles the spec once per variant
    ([`kernel_spec_variant`][rest_framework_services.service_specs.kernel_spec_variant.kernel_spec_variant]),
    hands ``dispatch`` the arguments, the principal and a ``Grant``, and
    translates what the kernel refuses into DRF's exceptions
    ([`translate_dispatch_errors`][rest_framework_services.service_specs.translate_dispatch_errors.translate_dispatch_errors]).
    The order of a call, the closed argument set, the Validator, affordances,
    preconditions, the run and the output re-read are the kernel's, documented
    on its ``dispatch``. Nothing is rendered here; render the result with
    [`render_spec_output`][rest_framework_services.dispatch.render_spec_output.render_spec_output].

    **Authorization is the caller's**, as it always was: the spec's
    ``permission_classes`` are never asked here. Over HTTP the view has run
    them; off HTTP the caller runs
    [`enforce_permissions`][rest_framework_services.dispatch.enforce_permissions.enforce_permissions]
    first and passes it as ``on_target_resolved``. So the dispatched variant
    carries no permissions and the call goes in under a ``Grant``.

    Args:
        spec: The
            [`ServiceSpec`][rest_framework_services.types.service_spec.ServiceSpec] or
            [`SelectorSpec`][rest_framework_services.types.selector_spec.SelectorSpec]
            to run.
        user: The acting user, the kernel's principal. ``None`` acts as DRF's
            unauthenticated user (``UNAUTHENTICATED_USER``), which is what every
            callable reading ``user`` is handed; the guard still sees ``None``.
        params: The caller's arguments, one flat mapping. A ``QueryDict`` is
            read as a query string is: a repeated key as a list where the
            parameter is an array, and each value coerced to the type declared.
            A list on a ``many`` spec, unless ``many_as_argument``.
        request: Registered as the call's ``request`` seed, so a provider, a
            selector, ``extend_queryset`` and the serializer context see it.
        view: Registered as the ``view`` seed. Its ``kwargs`` are the route: a
            declared ``UrlKwarg``, or a name the selector or target lookup
            reads, is taken from them when there, over ``params``; a declared
            ``UrlKwarg`` sent in ``params`` that the view lacks is put onto
            them, and a declared ``QueryParam`` onto ``request.query_params``,
            so a provider reading either finds it. That is for a view with no
            route of its own: on a live DRF view a declared ``UrlKwarg`` is
            the route's alone and a declared ``QueryParam`` the query
            string's alone, a ``params`` value of either name being dropped.
            A capture of a name the
            selector, or a service spec's target lookup, marks
            ``NotClientInput`` fills it: the lookup's is seeded for the call,
            beneath a seed of that name and the lookup's own provider, and an
            author's value for the name is seeded in its place. A route
            capture nothing declares, reads or hides is no argument.
        pool_seeds: The project's seeds. Registering ``request`` or ``view`` is
            refused, since the edge seeds both; a resolver declaring either is
            handed this call's.
        success_status: Accepted for the transports that pass it. The result
            carries no status: a transport answers its own.
        argument_binding: Whether a service spec with no ``input_serializer``
            takes the caller's values for its own parameters: only under
            ``SPREAD_AUTHOR_WINS``. ``AUTO`` is ``BUNDLE`` for a service, as it
            always was, so a caller's value reaches it only through what reads
            it - the Validator, the target lookup and the declared ``reads`` -
            and a request body never fills ``def destroy(*, instance,
            hard=False)``'s ``hard``. Under every binding the Validator's
            values reach a service by name beside ``data``, save one named
            like a key a callable of the call marks ``NotClientInput``: the
            client's value for that is in ``data`` alone, and the parameter
            gets a view hook's, the spec's ``kwargs`` provider's or
            ``input_data``'s value for it, or its default. A selector spec's
            parameters are its caller's under every binding. ``SPREAD_CALLER_WINS`` is refused
            with ``ValueError``.
        unknown_arguments: ``IGNORE`` (the default here; the kernel's is
            ``REJECT``) or ``REJECT``. ``PASSTHROUGH`` is refused with
            ``ValueError``. ``REJECT`` names every undeclared key in one
            ``"Unexpected argument(s): ..."`` message under
            ``non_field_errors``, a row's inside that row. A seed's name - a
            built-in one, ``request`` or one ``pool_seeds`` registers - is
            never one, and the caller's value for it is dropped; a caller's
            ``view`` is refused. Where a target lookup will query, an
            undeclared key is refused before it does, alone. A selector or
            target lookup taking a bare ``**kwargs``, and a service with no
            ``input_serializer`` taking one under ``SPREAD_AUTHOR_WINS``, drop
            an undeclared key rather than refuse it.
        on_target_resolved: The guard, called ``guard(spec, context,
            instance=target)`` with an ``OfflineContext`` of the ``user``,
            ``request`` and ``view`` passed here. A service spec's target is
            the row the caller handed as ``instance``, else the row or set its
            lookup found (a missing row is ``not_found`` and asks nothing), else
            ``None`` for a create; a ``many`` spec's is ``None``. A selector
            spec's is the row or the set its selector found, after the lookup
            and before the spec's own preconditions, and never a missing row.
        progress: The transport's progress reporter, combined with
            ``view_hooks.progress``.
        view_hooks: The calling view's resolved hook chains: an ``extra_kwargs``
            value for a name the caller fills (a read of the spec's callable or
            target lookup, or a field of a single call's input serializer, by
            its name or its ``source``) is put into the arguments over the
            client's value and checked as it would be; a value naming a seed is
            dropped; the rest of a single service call's outranks every key
            the input serializer hands back, a ``source="*"`` field or one its
            ``validate()`` adds, while the client's value for it stays in
            ``data`` and the bound ``serializer`` stays bound; on every other
            call the rest goes to ``dispatch(extra_kwargs=)``. The spec's
            ``kwargs`` provider is called here, from ``view`` and ``request``,
            and its answer is split the same way over the hooks', a key it
            names a seed refused; a key its ``TypedDict`` fills is never the
            caller's or a hook's. ``input_data`` is merged over the arguments,
            beneath the spec's ``input_data``; ``progress`` joins ``progress``.
        instance: The row the caller resolved itself (a view's
            ``get_object()``), handed to ``dispatch`` as its target; no lookup
            runs. ``None`` is a create on a spec that declares no target
            lookup; on one that declares a lookup it is ``not_found``, since
            that lookup admits no missing row.
        filter_data: The values the ``filter_set`` reads - the selector spec's
            own, or a service spec's target lookup's - restricted to its filter
            names, and the filter's only source for them when passed. A service
            spec's ``output_selector_spec.filter_set`` is bound to it whole, as
            passed, after the service has run, or to an empty mapping without
            it or under ``output_filter_from_call=False``.
        many_as_argument: Whether a ``many`` spec's rows arrive under
            ``spec.many_argument`` in an object, rather than as ``params``
            itself. Rows arriving bare are refused in the shape DRF's list
            serializer uses; under the argument, as the kernel keys them. A
            no-op on every other spec.
        partial: The request's partial flag (a ``PATCH``); ``None`` reads the
            spec's own.

    Returns:
        The kernel's ``DispatchResult``. A required row that does not exist is
        ``kind="not_found"``, for the transport to answer. A service spec whose
        ``output_selector_spec`` is a ``LIST`` with no ``selector`` answers
        ``kind="list"``, its value the service's own return, presented per row.

    Raises:
        ImproperlyConfigured: under that ``LIST`` declaration, the service
            returned something that is not a set of rows (a mapping, a ``str``
            or ``bytes``, a non-iterable, ``None``); its write stands.
        TypeError: ``spec`` is neither a ``ServiceSpec`` nor a ``SelectorSpec``.
        ValueError: a retired ``argument_binding`` or ``unknown_arguments``.
        InvalidArguments: the kernel refused the arguments.
    """
    state = EdgeCallState()
    # The call's state stays set while its refusal is reworded, so the input
    # serializer is described there as the call described it
    # (test_a_refusal_is_answered_in_the_wire_names).
    with (
        edge_call(state),
        edge_errors(
            spec,
            params,
            many_as_argument=many_as_argument,
            http=over_http(view),
            state=state,
        ),
    ):
        prepared = prepare_edge_call(
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
            instance=instance,
            filter_data=filter_data,
            many_as_argument=many_as_argument,
            partial=partial,
            state=state,
        )
        if isinstance(prepared, DispatchResult):
            return prepared
        result = dispatch(prepared.spec, **prepared.kwargs)
    return listed_output(spec, with_output_layers(result, state))


__all__ = ["dispatch_spec"]
