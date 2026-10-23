"""``as_kernel_spec`` - a drfs ``ServiceSpec`` or ``SelectorSpec`` compiled to the kernel's."""

from __future__ import annotations

import dataclasses
import datetime
import decimal
import enum
import inspect
import types
import typing
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from typing import Annotated, Any, Literal, cast, get_args, get_origin

from django.core.exceptions import ImproperlyConfigured
from django.db.models import QuerySet
from django_service_specs.adapters.filters.filter_set_parameters import filter_set_parameters
from django_service_specs.authorization.permission_check import PermissionCheck
from django_service_specs.authorization.unrestricted import Unrestricted
from django_service_specs.parameters.parameters import Parameter, Parameters
from django_service_specs.pool.provider_keys import provider_keys
from django_service_specs.pool.resolve_callable_kwargs import resolve_callable_kwargs
from django_service_specs.specs.kernel_spec import kernel_spec
from django_service_specs.specs.selector_spec import SelectorSpec as KernelSelectorSpec
from django_service_specs.specs.service_spec import ServiceSpec as KernelServiceSpec
from django_service_specs.types.is_async import is_async
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from typing_extensions import TypedDict

from rest_framework_services.service_specs.drf_permission_check import DRFPermissionCheck
from rest_framework_services.service_specs.serializer_presenter import SerializerPresenter
from rest_framework_services.service_specs.serializer_validator import SerializerValidator
from rest_framework_services.service_specs.utils import (
    SERVER_CHANNEL,
    UNREADABLE_WITHOUT_A_CALL,
    asked_of_the_caller,
    edge_call_state,
    fills_its_own,
    fragment_keywords,
    left_to_the_callable,
    server_channel,
    values_beside_filter,
    writable_input_names,
)
from rest_framework_services.types.affordance_declarer import AffordanceDeclarer
from rest_framework_services.types.field_marking import FieldMarking
from rest_framework_services.types.json_schema_registry import JsonSchemaRegistry
from rest_framework_services.types.marked_input_keys import marked_input_keys
from rest_framework_services.types.query_param import QueryParam
from rest_framework_services.types.read_input_description import read_input_description
from rest_framework_services.types.read_schema_markers import read_schema_markers
from rest_framework_services.types.reserved_pool_seeds import RESERVED_POOL_SEEDS
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.typed_dict_input import typed_dict_input
from rest_framework_services.types.unpack_typed_dict import unpack_typed_dict
from rest_framework_services.types.unset import UNSET, UnsetType
from rest_framework_services.types.url_kwarg import UrlKwarg
from rest_framework_services.types.utils import callable_type_hints

# The per-call seeds the DRF edge registers with the kernel. A ``serializer`` is
# not one: the Validator's own pool delivers it, and a Validator binding a name
# that is also a seed is refused.
_SEEDS: frozenset[str] = frozenset({"request", "view"})
# Names no reflected parameter becomes: the kernel's reserved seeds, the edge's
# seeds, and ``user`` and ``serializer``, which a callable receives from the
# pool rather than from a caller.
_NOT_ARGUMENTS: frozenset[str] = RESERVED_POOL_SEEDS | _SEEDS | {"user", "serializer"}
_SCALARS: tuple[tuple[Any, str, str | None], ...] = (
    # ``bool`` before ``int`` is not needed here, since types are matched by
    # identity, but the order still reads as the JSON types do.
    (bool, "boolean", None),
    (int, "integer", None),
    (float, "number", None),
    (str, "string", None),
    (decimal.Decimal, "string", "decimal"),
    (datetime.datetime, "string", "date-time"),
    (datetime.date, "string", "date"),
)
_ARRAYS: frozenset[Any] = frozenset({list, set, frozenset, tuple})


def as_kernel_spec(
    spec: Any,
    *,
    partial: bool | None = None,
    spread: bool = False,
    seeded: frozenset[str] = frozenset(),
) -> KernelServiceSpec | KernelSelectorSpec:
    """Compile ``spec``, a drfs ``ServiceSpec`` or ``SelectorSpec``, to the kernel's.

    What each drfs spec's own ``as_kernel_spec()`` answers, so the kernel's
    entry points - ``dispatch``, ``authorize``, the registry and the schemas -
    take a drfs spec as it is; they read it through ``kernel_spec``, which
    compiles it once per object. This function compiles afresh on every call.

    Each field lands on one seat:

    - ``input_serializer`` becomes the ``SerializerValidator``, and the output
      serializer the ``SerializerPresenter``, with the spec's
      ``field_audiences`` layered over the serializer's markings. A ``ServiceSpec``'s output serializer is its
      ``output_selector_spec``'s; one whose ``selector`` is ``None`` re-reads
      nothing, so it compiles to no output selector, and its serializer is the
      compiled spec's own presenter. Where that output is a ``LIST`` and the
      spec is not ``many``, the service returns the rows, which compiles to
      ``returns_many=True``: the kernel presents the return row by row and
      describes it as an array of the row.
    - ``permission_classes`` becomes one
      [`DRFPermissionCheck`][rest_framework_services.service_specs.drf_permission_check.DRFPermissionCheck];
      an empty sequence, which drfs reads as "no permissions, explicitly",
      becomes ``[Unrestricted()]``, the kernel's spelling of the same; ``None``
      compiles to no permissions, which the kernel's registry and dispatch
      refuse, so a drfs spec reaches a transport without a view only guarded.
    - ``reads`` is what the selector and the target lookup take, and under
      ``spread`` what a service with no ``input_serializer`` takes, read off
      their signatures as
      [`spec_to_json_schema`][rest_framework_services.jsonschema.spec_to_json_schema.spec_to_json_schema]
      reads them: named parameters and the keys of an ``Unpack``-ed
      ``TypedDict``, with ``InputRequired`` read as required and a
      ``NotClientInput`` key on any callable of the call left out. A named
      parameter with no default is required too, as the callable raises
      ``TypeError`` without it, unless a ``kwargs`` provider that says nothing
      of its keys may fill it or the callable is a wrapper that fills its own
      (``_reflected``). A row handed in as ``dispatch(target=...)`` does not
      excuse its lookup's key on this compile: the kernel checks the arguments
      before it reads the target, so a caller handing one still sends the key.
      The DRF edge's per-request variants (``kernel_spec_variant``) leave
      optional exactly the reads this rule requires
      (``required_for_having_no_default``), so a view handing the row it
      resolved, and each refusal stage the edge documents, answer as they
      always did. A misplaced marker is refused here, so at registration. Each declared ``UrlKwarg``
      and ``QueryParam`` in the spec's own ``reads`` is a Parameter too,
      winning over a reflected one of its name, required where the read says
      so or the parameter it replaces is (a read declaring a ``default`` is
      not: ``_merge``), and a read unless it is a shape (below); one the target lookup reads joins the lookup's reads.
      Every read taken off a signature is marked ``reflected`` and a declared
      one is not, so the kernel fills a registered pool seed's name in the
      former on every dispatch, ``kernel_spec(spec)``'s included, and still
      refuses the latter as an argument meeting a seed
      (test_the_kernel_fills_a_selector_s_reflected_read_from_the_seed,
      test_a_declared_read_named_like_the_seed_is_still_refused_by_the_kernel).
    - ``shapes`` is each declared ``QueryParam`` of the top-level spec that no
      callable of the call takes by name: not the service or selector, a
      precondition, a target lookup, a ``kwargs`` or ``input_data`` provider,
      a bare ``**kwargs`` or a ``filter_set`` field. It shapes what is
      rendered, as a serializer reading ``?fields=`` does, so the kernel hands
      it to the presenter alone, on ``PresentationContext.shapes``, rather than
      to a callable. One a callable names stays in ``reads``, and so does a
      nested spec's own. A name the ``filter_set`` reads is the filter's,
      as the filter field always won the schema. The pool's own names
      (``user``, ``request``, ``view``, ``serializer`` and the kernel's
      reserved seeds) are never arguments, and a declared one is refused. So
      is a declared read the service or selector, or one of its
      ``preconditions``, marks ``NotClientInput``: dispatch would hand that
      callable the client's value under the name it keeps the server's. A
      name only a target lookup hides is not refused; the lookup is never
      handed it, and the read is the service's argument. A
      route capture no signature names and no ``UrlKwarg`` declares is no
      argument at all, so it no longer reaches a spec.
    - The rest carries by name: ``atomic``, ``idempotent``, ``many``,
      ``many_argument``, ``allow_none``, ``affordances``, ``kwargs``,
      ``input_data``, ``preconditions``, ``progress_reporter``, ``metadata``,
      ``select_related``, ``prefetch_related``, ``annotations``,
      ``filter_set``. ``many`` takes the bounds of the list serializer DRF
      builds for the ``input_serializer``. ``extend_queryset``, which drfs calls
      as ``(queryset, view, request)``, is wrapped so the kernel, which binds it
      by name, calls it the same way; ``view`` and ``request`` are then the
      pool's registered seeds. A ``SelectorSpec``'s ``affordances`` map to the
      compiled form of each ``ServiceSpec``.

    ``partial`` overrides the spec's own for the variant the DRF edge builds
    per request (``PATCH``); ``None`` reads the spec's, where only ``True`` is
    partial. A spec with no ``input_serializer`` has nothing to relax, so it
    compiles unpartial.

    ``spread`` is ``ArgumentBinding.SPREAD_AUTHOR_WINS``: a service with no
    ``input_serializer`` then takes the caller's values for its own
    parameters. Without it, as ``AUTO`` and ``BUNDLE`` dispatch a service, a
    caller fills only what the Validator, the target lookup and the declared
    ``reads`` read, so ``def destroy(*, instance, hard=False)`` is never handed
    a ``hard`` the request sent (see ``_takes_its_signature``).

    ``seeded`` names seeds a dispatch registers beyond the pool's own, and
    leaves a signature's parameter of such a name - the selector's, a target
    lookup's, a spread service's - out of the compiled reads altogether
    (test_seeded_compiles_a_name_out_of_a_selector_s_reads_a_marked_read_stays,
    test_seeded_compiles_a_name_out_of_a_target_lookup_s_reads,
    test_seeded_compiles_a_name_out_of_a_spread_service_s_reads). Without it
    the parameter is a ``reflected`` read, which the kernel fills from a
    registered seed of its name on every dispatch but which stays among the
    compiled reads. The DRF edge no longer passes ``seeded``. A declared read
    of the name stays one, which the kernel refuses as an argument meeting a
    seed.

    **What the kernel refuses at construction is stripped or refused here**,
    naming the drfs field. Stripped, because drfs never read them: an output
    selector's ``kwargs``, an instance lookup's ``allow_none`` (the kernel would
    resolve a missing row to ``None``, an upsert, where drfs answers 404), a
    nested spec's ``permission_classes`` and a target lookup's
    ``output_serializer``. Refused, as drfs refuses them at ``as_view()``: a
    nested selector's ``preconditions`` and ``progress_reporter``, which would
    never run. Also refused: a ``selector`` of ``None`` anywhere but an output
    selector or an instance lookup, which compiles to no lookup, and ``field_audiences`` with no output serializer to layer them
    over.

    ``success_status``, ``document_service_error`` and ``response_finalizer``
    are the HTTP views', and none of them is carried. ``input_serializer_context``
    rides on the ``SerializerValidator`` and ``output_serializer_context`` on the
    ``SerializerPresenter``, which hands it the value it presents as ``result``
    for a service's output and as ``instance`` for a retrieve.
    """
    if isinstance(spec, AffordanceDeclarer):
        return _service(spec, partial=partial, spread=spread, seeded=seeded)
    return _selector(
        spec,
        role=None,
        hidden=frozenset(),
        declared=(),
        seeded=seeded,
        shaped=False,
        open_ended=_filled_by_an_untyped_provider(spec),
    )


def _service(
    spec: Any, *, partial: bool | None, spread: bool, seeded: frozenset[str]
) -> KernelServiceSpec:
    hidden = _owned_keys(
        [spec.service, *(spec.preconditions or ()), *_lookup_selectors(spec)],
    )
    declared, shapes = _shapes(
        spec,
        _declared(spec.reads, label="ServiceSpec"),
        callables=[spec.service, *(spec.preconditions or ()), *_lookup_selectors(spec)],
        providers=[spec.kwargs, spec.input_data, *_lookup_providers(spec)],
        filtered=frozenset(),
    )
    _refuse_hidden_reads(
        spec.reads, [spec.service, *(spec.preconditions or ())], label="ServiceSpec"
    )
    # One answer for every reflected parameter of the call, the target lookup's
    # included, as ``spec_to_json_schema`` gives one without ``supplied=``. Each
    # seat it reaches is held by its test of
    # ``TestAnUntypedProviderKeepsEveryParameterOptional``: the lookups by
    # test_a_service_spec_s_provider_keeps_its_target_lookup_s_parameter_optional,
    # the spread signature by test_a_spread_service_s_parameter.
    open_ended = _filled_by_an_untyped_provider(spec)
    instance = _nested(spec, "instance", hidden, declared, seeded, open_ended)
    collection = _nested(spec, "collection", hidden, declared, seeded, open_ended)
    target = instance or collection
    # In the lookup's order: the route a permission class reads is filled in it.
    taken = tuple(parameter.name for parameter in target.reads) if target is not None else ()
    _refuse_reads_named_like_fields(spec, lookup_reads=frozenset(taken))
    reads = _merge(
        # A seed of the call fills its name
        # (test_seeded_compiles_a_name_out_of_a_spread_service_s_reads).
        _spread_signature(spec, hidden=hidden | seeded, open_ended=open_ended)
        if _takes_its_signature(spec, spread)
        else [],
        _requiring(
            [parameter for parameter in declared if parameter.name not in taken],
            # The service's own parameters and its preconditions', which a read of
            # the name fills under ``AUTO`` and ``BUNDLE`` as it does under
            # ``spread`` (test_a_read_naming_a_parameter_with_no_default_compiles_required,
            # its ``service-under-auto`` and ``service-precondition-under-auto`` cases).
            _required_by(
                [spec.service, *(spec.preconditions or ())],
                hidden=hidden | seeded,
                open_ended=open_ended,
                registry=spec.json_schema_registry,
            ),
        ),
        skip=frozenset(taken),
    )
    output, presenter = _output(spec, shaped=bool(shapes))
    # Under a spreading binding a callable naming a read of the target lookup
    # is handed the caller's value for it by the edge, so the caller is asked
    # for one it left out (``_asking``). Under ``AUTO`` and ``BUNDLE`` the
    # parameter is the author's to fill
    # (test_without_a_spreading_binding_the_key_is_the_author_s_to_fill).
    asked = lookup_keys_the_service_names(spec, taken) if spread else frozenset()
    list_bounds = _list_bounds(spec)
    return KernelServiceSpec(
        # A spread service's parameters are its caller's arguments, which the
        # kernel refuses as missing where none arrived; a wrapper may fill them
        # itself (test_a_spread_service_s_wrapper_fills_the_parameter).
        service=_asking(
            _as_it_runs(spec.service) if _takes_its_signature(spec, spread) else spec.service,
            spec.service,
            asked,
        ),
        permissions=_permissions(spec, route_reads=taken),
        reads=reads,
        shapes=shapes,
        partial=compiled_partial(spec, partial),
        many=spec.many,
        many_argument=spec.many_argument,
        # Spelled out rather than splatted: the kernel's ``ServiceSpec`` takes
        # keywords of other types beside these two, which a splat of a
        # ``dict[str, int | None]`` could fill as far as a type checker knows.
        many_min_length=list_bounds[0],
        many_max_length=list_bounds[1],
        validator=(
            SerializerValidator(
                spec.input_serializer,
                serializer_context=spec.input_serializer_context,
                server_owned=hidden,
                json_schema_registry=spec.json_schema_registry,
            )
            if spec.input_serializer is not None
            else None
        ),
        instance_selector_spec=instance,
        collection_selector_spec=collection,
        output_selector_spec=output,
        presenter=presenter,
        # A ``LIST`` output with no re-read presents the service's return as the
        # rows, so ``None`` is never presented, whatever ``allow_none`` says: the
        # edge refuses it as the author's error, and the kernel's
        # ``can_present_nothing`` reads the compiled flag
        # (test_the_schema_the_kind_and_the_predicate_agree).
        allow_none=spec.allow_none and not _lists_its_return(spec),
        returns_many=_returns_many(spec),
        affordances=spec.affordances,
        atomic=spec.atomic,
        idempotent=spec.idempotent,
        preconditions=(
            [_asking(_as_it_runs(fn), fn, asked) for fn in spec.preconditions]
            if spec.preconditions
            else spec.preconditions
        ),
        kwargs=spec.kwargs,
        input_data=spec.input_data,
        progress_reporter=spec.progress_reporter,
        metadata=spec.metadata,
    )


def _lists_its_return(spec: Any) -> bool:
    """Whether ``spec`` declares a ``LIST`` output it does not re-read.

    A ``many`` spec re-reads nothing, whatever its output selector declares
    (``_output``).
    """
    output = spec.output_selector_spec
    # One arc to coverage: each conjunct is held by its own case of
    # test_only_a_list_output_with_no_re_read_turns_allow_none_off (no-output,
    # retrieve-no-re-read and list-re-read, in order), and ``many`` by
    # test_many_is_not_read (its ``LIST`` re-read declared with ``many``).
    return (
        output is not None
        and output.kind is SelectorKind.LIST
        and (output.selector is None or spec.many)
    )


def _returns_many(spec: Any) -> bool:
    """Whether ``spec``'s service returns the rows its output presents: the kernel's ``returns_many``.

    A ``LIST`` output with no re-read, so the kernel's own ``dispatch`` presents
    the return row by row and its output schema is an array of the row
    (test_the_kernel_s_own_dispatch_answers_the_rows). Not ``_lists_its_return``,
    which is true of a ``many`` spec too, and the kernel refuses
    ``returns_many`` beside ``many``. One arc to coverage, so each conjunct is
    held by its case of
    test_only_a_list_output_with_no_re_read_and_no_many_returns_many:
    ``no-output`` without the first, ``retrieve-output`` without the second,
    ``list-re-read`` without the third and ``many`` without the fourth.
    """
    output = spec.output_selector_spec
    return (
        output is not None
        and output.kind is SelectorKind.LIST
        and output.selector is None
        and not spec.many
    )


def _selector(
    spec: Any,
    *,
    role: str | None,
    hidden: frozenset[str],
    declared: Sequence[Parameter],
    audiences: Mapping[str, FieldMarking] | None = None,
    seeded: frozenset[str] = frozenset(),
    shaped: bool,
    open_ended: bool,
    inherited: JsonSchemaRegistry | None = None,
) -> KernelSelectorSpec:
    """A top-level selector spec (``role`` ``None``), or a service's nested one by its role.

    A nested lookup takes the declared Parameters naming what its selector
    reads; the rest are the service's. An output selector takes none: it
    re-reads the service's result, with no caller's arguments to read; the
    call's ``filter_data`` reaches its ``filter_set`` alone (``_output``).
    Only a top-level selector spec compiles ``shapes``: the kernel refuses
    them on a nested one. ``shaped`` says an output selector's service spec
    compiles some, which its presenter renders by. ``inherited`` is a nested
    spec's service spec's ``json_schema_registry``, which describes its reads
    where it declares none of its own (test_a_target_lookup_reads_its_own_registry,
    test_a_target_lookup_with_none_reads_the_service_spec_s).
    """
    registry = spec.json_schema_registry if spec.json_schema_registry is not None else inherited
    label = "SelectorSpec" if role is None else f"ServiceSpec.{role}_selector_spec"
    if role is not None:
        _refuse_nested_runs(spec, label)
    if spec.selector is None:
        raise ImproperlyConfigured(
            f"{label} declares no selector, and only an output selector may: there is "
            "nothing else to run. Declare the selector that reads the rows."
        )
    shapes = Parameters()
    if role is None:
        hidden = _owned_keys([spec.selector, *(spec.preconditions or ())])
        declared, shapes = _shapes(
            spec,
            _declared(spec.reads, label=label),
            callables=[spec.selector, *(spec.preconditions or ())],
            providers=[spec.kwargs],
            filtered=(
                filter_set_parameters(spec.filter_set).names()
                if spec.filter_set is not None
                else frozenset()
            ),
        )
        shaped = bool(shapes)
        _refuse_hidden_reads(spec.reads, [spec.selector, *(spec.preconditions or ())], label=label)
        audiences = spec.field_audiences
    reads = Parameters()
    if role != "output":
        # A seed of the call fills its name, so it is no argument
        # (test_seeded_compiles_a_name_out_of_a_selector_s_reads_a_marked_read_stays,
        # test_seeded_compiles_a_name_out_of_a_target_lookup_s_reads).
        reflected = _reflected(
            spec.selector, hidden=hidden | seeded, open_ended=open_ended, registry=registry
        )
        if role is not None:
            # A target lookup's own ``reads`` are declared as the spec's are, so
            # each is its parameter with the type it declares, and a string
            # the call carries is read against that type
            # (test_a_live_view_reads_them_from_its_query_string_and_route,
            # which fails without them). A name the spec declares too is the
            # spec's declaration, as the spec's ``reads`` always reached the
            # lookup (test_a_name_the_spec_and_its_lookup_both_read_is_the_spec_s).
            taken = _names(declared)
            own = [p for p in _declared(spec.reads or (), label=label) if p.name not in taken]
            declared = [p for p in (*declared, *own) if p.name in _names(reflected)]
        # A name the ``filter_set`` reads is the filter's: the kernel adds the
        # filter's Parameter itself, and refuses the same name from two sources.
        filtered = (
            filter_set_parameters(spec.filter_set).names()
            if spec.filter_set is not None
            else frozenset()
        )
        # A selector spec's preconditions take a read of their parameters' names as
        # the selector does (test_a_read_naming_a_parameter_with_no_default_compiles_required,
        # its ``selector-precondition`` case). A nested lookup declares none: it is
        # refused (``_refuse_nested_runs``), so this is empty there.
        declared = _requiring(
            declared,
            _required_by(
                spec.preconditions or (),
                hidden=hidden | seeded,
                open_ended=open_ended,
                registry=registry,
            ),
        )
        reads = _merge(reflected, declared, skip=filtered)
        shadowed = hidden & filtered
    else:
        filtered = shadowed = frozenset()
    return KernelSelectorSpec(
        kind=spec.kind,
        selector=_beside_its_filter(
            _on_the_server_s_channel(_as_it_runs(spec.selector), shadowed), spec, filtered
        ),
        # A nested selector's checks are never read: the dispatched spec's are.
        permissions=(
            _permissions(spec, route_reads=_route_reads(spec, reads)) if role is None else None
        ),
        reads=reads,
        shapes=shapes,
        # A target lookup's rows are the service's to consume, never presented.
        presenter=(
            _presenter(
                spec.output_serializer,
                audiences,
                label=label,
                context=spec.output_serializer_context,
                value_keyword=_value_keyword(spec, role),
                shaped=shaped,
                registry=registry,
            )
            if role in (None, "output")
            else None
        ),
        # An instance lookup's missing row is drfs' 404 whatever the nested flag
        # says; the kernel would hand the service ``None`` as an upsert's target.
        allow_none=spec.allow_none and role != "instance",
        select_related=spec.select_related,
        prefetch_related=spec.prefetch_related,
        annotations=spec.annotations,
        extend_queryset=(
            _positional(spec.extend_queryset) if spec.extend_queryset is not None else None
        ),
        filter_set=spec.filter_set,
        affordances=(
            {name: _affordance(declarer) for name, declarer in spec.affordances.items()}
            if spec.affordances is not None
            else None
        ),
        # drfs never ran an output selector's ``kwargs`` (the mutation's chains
        # apply), and the kernel refuses them there.
        kwargs=(
            # Only a target lookup's provider runs in an edge call: the
            # dispatched form of a selector spec carries none, as the edge
            # calls its provider itself (test_the_shaping_fields_carry).
            _recording_beside_its_filter(
                _answering_on_the_server_s_channel(spec.kwargs, shadowed),
                spec,
                filtered - hidden if role is not None else frozenset(),
            )
            if role != "output"
            else None
        ),
        preconditions=(
            [
                _beside_its_filter(
                    _on_the_server_s_channel(_as_it_runs(fn), shadowed), spec, filtered
                )
                for fn in spec.preconditions
            ]
            if spec.preconditions
            else spec.preconditions
        ),
        progress_reporter=spec.progress_reporter,
        metadata=spec.metadata,
    )


def _as_it_runs(fn: Any) -> Any:
    """``fn``, left to fill its own parameters where it is a wrapper that may (``fills_its_own``).

    Read off the author's callable, before the compiler's own forwarding
    callables wrap it: those carry ``__wrapped__`` and a ``__signature__`` too,
    and every one of them would read as such a wrapper. Without it a selector,
    a target lookup's selector, a precondition or a spread service whose
    decorator fills a parameter is refused for it
    (test_a_wrapper_that_fills_the_parameter_itself_is_not_refused,
    test_a_target_lookup_s_wrapper_fills_the_parameter,
    test_a_spread_service_s_wrapper_fills_the_parameter).
    """
    return left_to_the_callable(fn) if fills_its_own(fn) else fn


def _spread_signature(spec: Any, *, hidden: frozenset[str], open_ended: bool) -> list[Parameter]:
    """A spread service's parameters, and its preconditions' beside a bare ``**kwargs``.

    A bare ``**kwargs`` leaves a spread service's surface open, so a
    precondition's parameter is
    one the caller sends, as on 0.57.0
    (test_the_caller_s_value_reaches_the_precondition), each name once, in the
    service's declaration where it names it too
    (test_two_preconditions_naming_one_parameter_share_the_caller_s_value,
    test_a_parameter_the_service_names_too_is_the_service_s_declaration). A
    service closing its signature, by named parameters alone or an
    ``Unpack``-ed ``TypedDict``, declares none
    (test_a_closed_service_declares_no_precondition_parameter, its ``named``
    and ``typed-dict`` cases). A name the server owns is never one
    (test_a_name_the_server_owns_never_takes_the_caller_s_value), and the
    caller skips the target lookup's reads
    (test_a_read_of_the_target_lookup_stays_the_lookup_s).
    """
    registry = spec.json_schema_registry
    reflected = _reflected(spec.service, hidden=hidden, open_ended=open_ended, registry=registry)
    if not _takes_any_name(spec.service):
        return reflected
    names = set(_names(reflected))
    for precondition in spec.preconditions or ():
        for parameter in _reflected(
            precondition, hidden=hidden, open_ended=open_ended, registry=registry
        ):
            if parameter.name not in names:
                names.add(parameter.name)
                reflected.append(parameter)
    return reflected


def _takes_any_name(fn: Callable[..., Any]) -> bool:
    """Whether ``fn`` takes a bare ``**kwargs``, which leaves its surface open.

    An annotated one that is no ``Unpack``-ed ``TypedDict`` reads as bare, as
    ``_reflected`` reads it, one whose annotation cannot be resolved included
    (test_an_annotation_that_cannot_be_resolved_reads_as_open). One arc to
    coverage, so each conjunct is named: the ``named`` case of
    test_a_closed_service_declares_no_precondition_parameter fails without the
    first, and its ``typed-dict`` case without the second.
    """
    hints = callable_type_hints(fn)
    return any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        and unpack_typed_dict(hints.get(name)) is None
        for name, parameter in inspect.signature(fn).parameters.items()
    )


def lookup_keys_the_service_names(spec: Any, lookup_reads: Iterable[str]) -> frozenset[str]:
    """The target lookup's reads a service spec's service asks a spreading caller for by name.

    A keyword parameter or a key of its ``Unpack``-ed ``TypedDict``, as 0.57.0
    read the service's surface
    (test_a_typed_dict_key_takes_the_caller_s_value_and_is_never_asked_for);
    never an open ``**kwargs``, which takes none of the lookup's reads
    (test_an_open_service_naming_the_key_takes_it_by_name_alone). The service
    alone: a precondition naming one is handed it only beside a service that
    asks for it (test_a_precondition_naming_a_key_the_service_does_not_is_the_author_s_error).
    Beside an input serializer only a writable field of the name delivers it,
    so a name no field takes is no caller's to send
    (test_a_serializer_with_no_field_for_the_key_leaves_it_to_the_author).
    """
    # Names alone, so whether one is required is not asked.
    named = _names(_reflected(spec.service, hidden=frozenset(), open_ended=True))
    if spec.input_serializer is not None:
        named &= writable_input_names(spec)
    return frozenset(lookup_reads) & named


def _asking(runs: Any, own: Any, names: frozenset[str]) -> Any:
    """``runs``, asking the caller for each of ``names`` its author's ``own`` requires.

    ``names`` are a target lookup's reads, which a spread service and its
    preconditions take from the caller by name, as on 0.57.0
    (test_the_service_takes_the_caller_s_value_where_the_route_scopes_the_lookup,
    test_a_precondition_naming_the_key_takes_the_caller_s_value). One left out
    is the caller's ``400`` (``asked_of_the_caller``) where ``own`` gives it no
    default (test_a_parameter_with_a_default_takes_the_caller_s_value_or_keeps_its_own)
    and fills none of its parameters itself
    (test_a_wrapper_filling_the_key_itself_runs). Read off ``own``, the
    author's callable, as ``fills_its_own`` requires: ``runs`` may be a
    forwarding callable reporting a default ``own`` does not have. ``runs``
    itself where nothing is asked, so a spec naming no lookup read compiles the
    callable it always did.
    """
    if fills_its_own(own):
        return runs
    # One arc to coverage, so each conjunct is named:
    # test_a_parameter_with_a_default_takes_the_caller_s_value_or_keeps_its_own
    # fails without the first, and
    # test_a_positional_only_parameter_is_never_asked_for (which no call
    # passes by name) without the second.
    required = frozenset(
        parameter.name
        for parameter in inspect.signature(own).parameters.values()
        if parameter.default is inspect.Parameter.empty
        and parameter.kind
        in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    )
    # Only the lookup's reads: any other parameter is either the kernel's to
    # refuse or no caller's to fill
    # (test_a_parameter_no_caller_can_fill_stays_the_author_s_error).
    asked = required & names
    return asked_of_the_caller(runs, asked) if asked else runs


def _on_the_server_s_channel(fn: Any, shadowed: frozenset[str]) -> Any:
    """``fn``, taking each ``shadowed`` parameter on its server channel instead.

    A name a filter reads and a callable marks ``NotClientInput`` is two
    things, as it was in 0.57.0: the filter's argument, the client's value,
    and the callable's parameter, which only the server fills. The kernel
    hands a callable every value of its pool under the parameter's name, the
    filter's argument included, so the parameter is taken under its
    ``server_channel`` name, which no read declares and so no client fills
    (test_a_client_naming_the_server_s_channel_reaches_nothing), and handed
    back under its own (test_the_client_s_value_filters_and_never_reaches_the_parameter).
    An ``async def`` selector stays one, as the kernel awaits it by its kind
    (test_an_async_selector_s_parameter_is_kept_from_the_client_too).
    """
    signature = inspect.signature(fn)
    taken = shadowed & signature.parameters.keys()
    if not taken:
        return fn

    def own_names(kwargs: Mapping[str, Any]) -> dict[str, Any]:
        return {name.removeprefix(SERVER_CHANNEL): value for name, value in kwargs.items()}

    if is_async(fn):

        async def channelled(**kwargs: Any) -> Any:
            return await fn(**own_names(kwargs))
    else:

        def channelled(**kwargs: Any) -> Any:
            return fn(**own_names(kwargs))

    channelled.__name__ = fn.__name__
    channelled.__qualname__ = fn.__qualname__
    channelled.__module__ = fn.__module__
    # Read for an annotated ``**rest`` in ``fn``'s module, as ``_beside_its_filter``
    # (test_a_lookup_s_hidden_parameter_beside_a_bare_kwargs_takes_no_client_value).
    channelled.__annotations__ = dict(getattr(fn, "__annotations__", {}))
    cast(Any, channelled).__wrapped__ = fn
    cast(Any, channelled).__signature__ = signature.replace(
        parameters=[
            parameter.replace(name=server_channel(parameter.name))
            if parameter.name in taken
            else parameter
            for parameter in signature.parameters.values()
        ]
    )
    return channelled


def _beside_its_filter(fn: Any, spec: Any, filtered: frozenset[str]) -> Any:
    """``fn``, handed under each name ``spec``'s filter reads what 0.57.0 handed it there.

    The kernel's pool holds a filter's argument under the filter's name, and
    hands it to every callable taking that name or a bare ``**kwargs``. The
    filter's value was never a callable's in 0.57.0: ``filter_data`` fed the
    ``FilterSet`` alone, and a selector parameter named like a filter took the
    caller's argument as sent, or its default
    (test_filter_data_never_reaches_the_selector,
    test_the_selector_takes_the_caller_s_argument_as_sent). So within the
    edge call that prepared them (``values_beside_filter``) each such name
    the pool holds is dropped, and the edge's value for it handed instead,
    where ``fn`` takes the name. Outside one ``fn`` is handed the pool, as
    the kernel hands it. A name ``fn`` takes on its server channel
    (``_on_the_server_s_channel``) is no filter's name here, and passes.
    """
    signature = inspect.signature(fn)
    open_ended = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )
    taken = filtered if open_ended else filtered & signature.parameters.keys()
    # A callable taking none of the filter's names is handed nothing of it,
    # and is the spec's own (test_the_shaping_fields_carry).
    if not taken:
        return fn

    def as_handed(kwargs: dict[str, Any]) -> dict[str, Any]:
        values = values_beside_filter(spec)
        if values is None:
            return kwargs
        kept = {name: value for name, value in kwargs.items() if name not in filtered}
        kept.update({name: values[name] for name in taken if name in values})
        return kept

    if is_async(fn):

        async def beside(**kwargs: Any) -> Any:
            return await fn(**as_handed(kwargs))
    else:

        def beside(**kwargs: Any) -> Any:
            return fn(**as_handed(kwargs))

    beside.__name__ = getattr(fn, "__name__", "selector")
    beside.__qualname__ = getattr(fn, "__qualname__", beside.__name__)
    beside.__module__ = fn.__module__
    # ``fn``'s signature and annotations, resolved in ``fn``'s module: an
    # annotated ``**rest`` is read from them (``resolved_annotation`` unwraps
    # for the globals), which this module cannot resolve
    # (test_a_lookup_s_selector_takes_the_caller_s_argument_and_never_filter_data).
    # ``inspect.signature`` stops at ``__signature__`` before ``__wrapped__``.
    beside.__annotations__ = dict(getattr(fn, "__annotations__", {}))
    cast(Any, beside).__wrapped__ = fn
    cast(Any, beside).__signature__ = signature
    return beside


def _recording_beside_its_filter(provider: Any, spec: Any, names: frozenset[str]) -> Any:
    """``provider``, its value for each of ``names`` also handed to ``spec``'s callables.

    A target lookup's own ``kwargs`` provider runs inside the kernel's call,
    after the edge prepared what the lookup's selector is handed under its
    filter's names (``values_beside_filter``), and its value outranked the
    caller's there in 0.57.0 (test_a_lookup_s_provider_value_reaches_its_selector).
    """
    # One arc to coverage: without the ``None`` check a lookup with no provider
    # fails (test_a_target_lookup_s_filter_takes_what_its_filter_set_reads),
    # without ``names`` a selector spec's own provider is replaced
    # (test_the_shaping_fields_carry).
    if provider is None or not names:
        return provider

    def recording(**kwargs: Any) -> Any:
        answered = provider(**kwargs)
        values = values_beside_filter(spec)
        if values is not None:
            values.update({name: answered[name] for name in names if name in answered})
        return answered

    cast(Any, recording).__signature__ = inspect.signature(provider)
    # The kernel reads the keys a typed provider fills off its return
    # annotation, resolved in the globals of what ``__wrapped__`` names, so a
    # read the provider fills stays relaxed rather than asked of the caller
    # (test_a_target_lookup_s_typed_provider, its ``beside-a-filter`` cases).
    cast(Any, recording).__wrapped__ = provider
    recording.__annotations__ = dict(getattr(provider, "__annotations__", {}))
    recording.__name__ = getattr(provider, "__name__", "kwargs")
    recording.__qualname__ = getattr(provider, "__qualname__", recording.__name__)
    recording.__module__ = provider.__module__
    return recording


def _answering_on_the_server_s_channel(provider: Any, shadowed: frozenset[str]) -> Any:
    """The spec's ``kwargs`` provider, answering each ``shadowed`` key on its server channel.

    Its value is the parameter's and never the filter's, as in 0.57.0, where
    the filter read only the client's
    (test_the_provider_s_value_reaches_the_parameter_and_not_the_filter).
    """
    # One arc to coverage: with no name to carry, the provider is the spec's
    # own, which the compiled spec is read for
    # (test_the_client_s_value_filters_and_never_reaches_the_parameter fails
    # without the first condition, test_the_shaping_fields_carry without the
    # second).
    if provider is None or not shadowed:
        return provider

    def channelled(**kwargs: Any) -> Any:
        return {
            server_channel(key) if key in shadowed else key: value
            for key, value in provider(**kwargs).items()
        }

    cast(Any, channelled).__signature__ = inspect.signature(provider)
    channelled.__annotations__ = _answered_on_the_server_s_channel(provider, shadowed)
    channelled.__name__ = getattr(provider, "__name__", "kwargs")
    channelled.__qualname__ = getattr(provider, "__qualname__", channelled.__name__)
    channelled.__module__ = provider.__module__
    return channelled


def _answered_on_the_server_s_channel(provider: Any, shadowed: frozenset[str]) -> dict[str, Any]:
    """``provider``'s annotations, its return declaring each ``shadowed`` key on its server channel.

    The kernel reads which names a typed provider fills off its return
    annotation (``provider_keys``), and takes a caller's value for such a name
    as no argument. A ``shadowed`` key is answered on its server channel and
    fills no argument of its name, which is the filter's and the caller's, so
    the return declares the keys as they are answered rather than as the
    provider names them, and each other key stays filled or declinable as the
    provider declares it
    (test_through_the_kernel_a_typed_provider_s_value_reaches_the_parameter_and_not_the_filter:
    without the renaming both its cases fail, its ``reach`` fails with a
    declinable key read as filled, and with the provider's own return its
    ``evaluated`` case fails and its ``postponed`` case reads the provider as
    untyped and asks the caller for ``scope``, as
    test_a_target_lookup_s_typed_provider_s_value_reaches_the_parameter_and_not_the_filter
    does for a lookup's provider). Read here, off the provider itself, because a postponed return annotation
    names a type in the provider's module, which this wrapper's globals cannot
    resolve.
    """
    annotations = dict(getattr(provider, "__annotations__", {}))
    keys = provider_keys(provider)
    # A provider whose return says nothing about its keys stays one that does
    # not say (test_the_provider_s_value_reaches_the_parameter_and_not_the_filter
    # fails without the check, its provider a lambda).
    if keys is not None:

        def answered_as(key: str) -> str:
            return server_channel(key) if key in shadowed else key

        # A declinable key is annotated ``UnsetType``, which is how the kernel
        # reads one (the ``reach`` of the test above fails with ``Any``).
        fields: dict[str, Any] = {answered_as(key): Any for key in keys.filled}
        fields.update({answered_as(key): UnsetType for key in keys.declinable})
        # The keys are the provider's, known only here, so the fields cannot be
        # the literal a checker reads a functional ``TypedDict`` from.
        annotations["return"] = TypedDict("Answered", fields)  # ty: ignore[invalid-argument-type]
    return annotations


def _nested(
    spec: Any,
    role: str,
    hidden: frozenset[str],
    declared: Sequence[Parameter],
    seeded: frozenset[str],
    open_ended: bool,
) -> KernelSelectorSpec | None:
    nested = getattr(spec, f"{role}_selector_spec")
    # An instance lookup with no ``selector`` is no lookup, as drfs always read
    # it: an HTTP view resolves the row with its own ``get_object()`` and hands
    # it in, and a call with no row is a create
    # (test_selectorless_instance_spec_falls_back_to_get_object). A collection
    # lookup has no such fallback, so one with no selector is still refused.
    if nested is None or (role == "instance" and nested.selector is None):
        return None
    return _selector(
        nested,
        role=role,
        hidden=hidden,
        declared=declared,
        seeded=seeded,
        shaped=False,
        open_ended=open_ended,
        inherited=spec.json_schema_registry,
    )


def _output(
    spec: Any, *, shaped: bool
) -> tuple[KernelSelectorSpec | None, SerializerPresenter | None]:
    """The compiled output selector, or the presenter a selector-less one moves onto the service.

    ``shaped`` says the service spec compiles ``shapes``, which reach either
    presenter.
    """
    output = spec.output_selector_spec
    if output is None:
        _presenter(None, spec.field_audiences, label="ServiceSpec", shaped=shaped)
        return None, None
    audiences = _audiences(output, spec.field_audiences)
    # A ``many`` service's result is its list of rows, which a selector re-reading
    # one result cannot take, so its output selector is never run: its
    # serializer presents each row, as drfs always presented them
    # (test_off_http_the_service_s_rows_are_the_value).
    if output.selector is not None and not spec.many:
        compiled = _selector(
            output,
            role="output",
            hidden=frozenset(),
            declared=(),
            audiences=audiences,
            shaped=shaped,
            # An output selector reflects nothing: it re-reads the service's result.
            open_ended=False,
            inherited=spec.json_schema_registry,
        )
        # The kernel binds an output ``filter_set`` to the re-read's pool, which
        # carries the service's return and nothing of the call. So where the
        # call narrows the re-read, the filter runs in drfs' ``extend_queryset``
        # seat instead, the step the kernel's filter would follow, and the
        # kernel's own is dropped so the FilterSet is bound once. Compiled with
        # it first, so a ``filter_set`` that is no FilterSet is refused as before.
        # One arc to coverage, a condition each:
        # test_a_spec_can_keep_the_call_out_of_the_re_read fails without the
        # flag, and test_output_selector_refetches_and_renders
        # without the ``filter_set`` check.
        if spec.output_filter_from_call and output.filter_set is not None:
            compiled = dataclasses.replace(
                compiled,
                filter_set=None,
                extend_queryset=_filtering_from_the_call(output.extend_queryset, output.filter_set),
            )
        return compiled, None
    _refuse_nested_runs(output, "ServiceSpec.output_selector_spec")
    return None, _presenter(
        output.output_serializer,
        audiences,
        label="ServiceSpec",
        context=output.output_serializer_context,
        value_keyword=_value_keyword(output, "output", many=spec.many),
        shaped=shaped,
        # The output selector's own, else the service spec's
        # (test_a_selector_less_output_reads_its_own_registry_else_the_service_spec_s).
        registry=(
            output.json_schema_registry
            if output.json_schema_registry is not None
            else spec.json_schema_registry
        ),
    )


def _value_keyword(spec: Any, role: str | None, *, many: bool = False) -> str | None:
    """The name an output context callable reads the presented value by, as drfs offers it.

    ``result`` for a service's output, ``instance`` for a retrieve, and none
    for what the kernel presents row by row: a list, whose callable drfs hands
    the whole ``page``, and a ``many`` service's output, whose ``result`` is
    the whole list of rows. Neither is in hand when one row is presented, and a
    row under either name would answer a callable written for the whole set
    (test_a_many_service_s_output_callable_is_offered_no_row_by_name).
    """
    if role == "output":
        return None if many else "result"
    return "instance" if spec.kind == SelectorKind.RETRIEVE else None


def _presenter(
    serializer: Any,
    audiences: Mapping[str, FieldMarking] | None,
    *,
    label: str,
    context: Callable[..., Mapping[str, Any]] | None = None,
    value_keyword: str | None = None,
    shaped: bool,
    registry: JsonSchemaRegistry | None = None,
) -> SerializerPresenter | None:
    """The ``SerializerPresenter`` for ``serializer``, or none where there is no serializer.

    ``shaped`` is whether the spec it presents for compiles ``shapes``, whose
    values reach the presenter on ``PresentationContext.shapes`` and may change
    which keys it renders. Each of its three sites, a selector spec's, a
    service's own and an output selector's, is held by its case of
    test_the_presenter_is_told_whether_the_call_compiles_a_shape.
    """
    if serializer is None:
        if audiences:
            raise ImproperlyConfigured(
                f"{label} declares field_audiences and no output serializer. "
                "field_audiences layers markings over the output serializer's fields, and "
                "with none there is nothing to mark; declare the output serializer, or "
                "remove field_audiences."
            )
        return None
    return SerializerPresenter(
        serializer,
        field_audiences=audiences,
        serializer_context=context,
        value_keyword=value_keyword,
        # test_the_compiled_output_says_no_key_is_present_under_a_shape
        shaped=shaped,
        json_schema_registry=registry,
    )


def _audiences(
    output: Any, outer: Mapping[str, FieldMarking] | None
) -> Mapping[str, FieldMarking] | None:
    # The service spec's declaration over its output selector's own, since the
    # service spec is the operation a mount names.
    merged = {**(output.field_audiences or {}), **(outer or {})}
    return merged or None


def compiled_partial(spec: Any, partial: bool | None) -> bool:
    """The ``partial`` a service spec compiles with: the request's flag, else the spec's own.

    A spec with no ``input_serializer`` has nothing to relax, so it compiles
    unpartial.
    """
    flag = (spec.partial is True) if partial is None else partial
    return flag and spec.input_serializer is not None


def _refuse_nested_runs(spec: Any, label: str) -> None:
    for name, reason in (
        ("preconditions", "Declare them on the ServiceSpec, whose pool carries the target."),
        ("progress_reporter", "Declare it on the ServiceSpec, whose service is the run."),
    ):
        if getattr(spec, name) is not None:
            raise ImproperlyConfigured(
                f"{label} declares {name}, which never run on a nested selector. {reason}"
            )


def _permissions(spec: Any, *, route_reads: Sequence[str]) -> list[PermissionCheck] | None:
    classes = spec.permission_classes
    if classes is None:
        return None
    if not classes:
        return [Unrestricted()]
    declared = tuple(spec.reads)
    query_params = [entry for entry in declared if isinstance(entry, QueryParam)]
    # A declared ``QueryParam`` is the query string's, wherever the lookup reads it.
    queried = {entry.name for entry in query_params}
    return [
        DRFPermissionCheck(
            classes,
            url_kwargs=[entry for entry in declared if isinstance(entry, UrlKwarg)],
            query_params=query_params,
            route_reads=[name for name in route_reads if name not in queried],
        )
    ]


def _affordance(declarer: Any) -> KernelServiceSpec:
    # The one compiled spec every entry point reads, so the kernel answers a row's
    # affordances against the spec a transport dispatches. drfs' ``SelectorSpec``
    # refuses anything but a ``ServiceSpec`` here, which compiles to the kernel's.
    return cast(KernelServiceSpec, kernel_spec(declarer))


def _route_reads(spec: Any, reads: Parameters) -> tuple[str, ...]:
    # A retrieve's reads are its row's key, which a route captures; a list's are
    # its filters, which a query string carries.
    return tuple(p.name for p in reads) if spec.kind is SelectorKind.RETRIEVE else ()


def _lookup_selectors(spec: Any) -> Iterator[Callable[..., Any]]:
    for role in ("instance", "collection"):
        nested = getattr(spec, f"{role}_selector_spec")
        # One arc to coverage: every test of a spec missing either lookup fails
        # without the first conjunct, and
        # ``test_a_selector_of_none_outside_an_output_is_refused`` without the
        # second, which leaves the refusal naming the missing selector to run.
        if nested is not None and nested.selector is not None:
            yield nested.selector


def _lookup_providers(spec: Any) -> Iterator[Callable[..., Any]]:
    for role in ("instance", "collection"):
        nested = getattr(spec, f"{role}_selector_spec")
        if nested is not None and nested.kwargs is not None:
            yield nested.kwargs


def _shapes(
    spec: Any,
    declared: list[Parameter],
    *,
    callables: Sequence[Callable[..., Any]],
    providers: Sequence[Callable[..., Any] | None],
    filtered: frozenset[str],
) -> tuple[list[Parameter], Parameters]:
    """``declared`` split into the reads and the ``shapes``: each ``QueryParam`` no callable names.

    A ``QueryParam`` is read-shaping by its own docstring: a value a serializer
    reads off ``request.query_params``. The kernel hands a shape to the
    presenter alone, never to a callable, so one that any callable of the
    call takes by name stays a read, as it is handed today. Counted, each kind
    held by its case of test_a_query_param_a_callable_names_stays_a_read:

    - ``callables``, the service or selector (``service``, ``selector``), its
      preconditions (``service-precondition``, ``selector-precondition``) and a
      target lookup's selector (``instance-lookup``, ``collection-lookup``),
      by a named parameter or an ``Unpack``-ed key (``service-unpacked-key``);
    - ``providers``, the spec's ``kwargs`` (``service-provider``,
      ``selector-provider``), its ``input_data`` (``input-data``) and a target
      lookup's ``kwargs`` (``lookup-provider``), which are called from the
      pool the reads join, by a named parameter, and also by a key a typed one
      is annotated to fill (``service-provider-fills``,
      ``selector-provider-fills``), which the kernel refuses beside a shape;
    - any of them taking a bare ``**kwargs``, which takes every name
      (``service-kwargs``, ``selector-kwargs``, ``service-provider-kwargs``);
    - ``filtered``, a selector spec's ``filter_set`` fields, which are the
      filter's reads (``selector-filter``).

    The answer is read off the drfs spec alone, so every variant of it compiles
    the same ``shapes`` (test_every_variant_of_one_spec_compiles_the_same_shapes).
    A ``UrlKwarg`` is the route's, never a shape (test_a_url_kwarg_is_never_a_shape).
    """
    queried = {entry.name for entry in spec.reads if isinstance(entry, QueryParam)}
    named: set[str] = set(filtered)
    for fn in (*callables, *(provider for provider in providers if provider is not None)):
        if _takes_any_name(fn):
            return declared, Parameters()
        # Names alone, so whether one is required is not asked.
        named |= _names(_reflected(fn, hidden=frozenset(), open_ended=True))
    for provider in providers:
        keys = provider_keys(provider)
        if keys is not None:
            named |= keys.filled | keys.declinable
    shaping = queried - named
    return (
        [parameter for parameter in declared if parameter.name not in shaping],
        Parameters(tuple(parameter for parameter in declared if parameter.name in shaping)),
    )


def _takes_its_signature(spec: Any, spread: bool) -> bool:
    """Whether a service's own parameters are its caller's arguments.

    Only under a spreading binding, and only with no ``input_serializer``,
    which otherwise declares the input, as ``spec_to_json_schema`` lists them.
    Without the binding a service's parameters are the author's to fill, as
    ``BUNDLE`` always left them: a view hook, the spec's ``kwargs`` provider or
    a default, never a request's body. One arc to coverage, so each conjunct is
    held by the test that fails without it:
    test_a_client_value_never_fills_a_service_parameter_off_http (the binding)
    and test_a_serializer_keeps_its_input_under_a_spreading_binding (no
    serializer). A ``many`` spec always has one: the kernel refuses ``many``
    with no validator.
    """
    return spread and spec.input_serializer is None


def _filled_by_an_untyped_provider(spec: Any) -> bool:
    """Whether ``spec``, or its target lookup, has a ``kwargs`` provider that says nothing about its keys.

    Such a provider may answer any name, so a reflected parameter it might fill
    is never required of the caller (``_reflected``). A typed one says which
    keys it fills, and the kernel relaxes exactly those. The spec's own
    provider is held by test_a_selector_spec_s_own_provider and
    test_a_service_spec_s_provider_keeps_its_target_lookup_s_parameter_optional,
    the lookup's by test_a_target_lookup_s_own_provider. A selector spec has
    no target lookup.
    """
    lookups = (getattr(spec, f"{role}_selector_spec", None) for role in ("instance", "collection"))
    providers = [spec.kwargs, *(lookup.kwargs for lookup in lookups if lookup is not None)]
    # ``provider_keys`` answers ``None`` only for a provider that says nothing:
    # an absent one fills nothing, which it says.
    return any(provider_keys(provider) is None for provider in providers)


def required_for_having_no_default(spec: Any) -> tuple[frozenset[str], frozenset[str]]:
    """The reads ``as_kernel_spec(spec)`` requires for having no default alone: the spec's, then its target lookup's.

    Each is a parameter of the callable the read is reflected from - a
    selector spec's selector, a service spec's service and preconditions
    under ``spread``, a target lookup's selector - that takes a keyword and
    has no default (``_reflected``). The DRF edge relaxes exactly these in
    each variant it dispatches (``kernel_spec_variant``), so a row a view hands in still answers a lookup's key, and a missing
    argument is refused at the stage the edge documents rather than up front.
    The rest of what is required stays required, each held by its case of
    test_the_edge_s_variant_relaxes_exactly_the_reads_the_rule_asks_for:
    an ``InputRequired`` parameter (``input-required``), a required key of an
    ``Unpack``-ed ``TypedDict``, which is no keyword of the signature
    (``typed-dict-key``), and a declared read of the name that says
    ``required=True``, which wins over the reflected one (a declared read
    that does not is relaxed as the parameter it names, ``_saying_required``): a selector spec's (``declared-by-the-selector-spec``), a
    service spec's for its own reads (``declared-by-the-service-spec``) or for
    its lookup's (``declared-by-the-spec-for-its-lookup``), or the lookup's own
    (``declared-by-the-lookup``). A spread service's preconditions are named
    beside it (``spread-precondition``).
    """
    declared = _saying_required(spec.reads)
    if not isinstance(spec, AffordanceDeclarer):
        # A top-level selector's preconditions take a read as the selector does. A
        # name any of them marks ``InputRequired`` stays required whichever callable
        # takes it plain (the ``input-required-beside-a-precondition`` case of
        # test_the_edge_s_variant_relaxes_exactly_the_reads_the_rule_asks_for).
        callables = [spec.selector, *(spec.preconditions or ())]
        return (
            frozenset().union(*map(_no_default, callables))
            - frozenset().union(*map(_input_required, callables))
            - declared,
            frozenset(),
        )
    own = _no_default(spec.service).union(
        *(_no_default(precondition) for precondition in spec.preconditions or ())
    )
    lookup: frozenset[str] = frozenset()
    for role in ("instance", "collection"):
        nested = getattr(spec, f"{role}_selector_spec")
        if nested is not None and nested.selector is not None:
            # A name the spec or the lookup declares a plain read for is relaxed too,
            # whatever makes it required: the lookup never runs for a row a view
            # hands in, so a marked or ``TypedDict`` key it takes is not asked of the
            # caller either (test_a_handed_row_is_answered_without_a_key_the_lookup_marks_and_the_spec_declares,
            # its marked and ``TypedDict`` cases). One that is not the lookup's is no
            # compiled read of it, so nothing is relaxed.
            lookup |= (
                _no_default(nested.selector)
                | {entry.name for entry in (*(spec.reads or ()), *(nested.reads or ()))}
            ) - _saying_required(nested.reads)
    return own - declared, lookup - declared


def _saying_required(entries: Sequence[UrlKwarg | QueryParam] | None) -> frozenset[str]:
    """The names of the declared reads that say ``required=True``: required for a reason of their own.

    A declared read that says nothing of it compiles required only for the
    parameter it names having no default (``_merge``), which is the rule the
    edge relaxes, so it is not named here
    (the ``*-declared-without-required`` cases of
    test_the_edge_s_variant_relaxes_exactly_the_reads_the_rule_asks_for).
    The ``declared-by-the-*`` cases of the same test hold the other direction:
    a read saying ``required=True`` stays required at the edge.
    test_the_edge_answers_it_without_a_key_the_spec_declares holds the claim end
    to end. A ``QueryParam`` has no ``required``, so it never is.
    """
    return frozenset(entry.name for entry in entries or () if getattr(entry, "required", False))


def _input_required(fn: Callable[..., Any]) -> frozenset[str]:
    """The names ``fn`` marks ``InputRequired``, which are required for that reason alone."""
    hints = callable_type_hints(fn)
    return frozenset(
        name
        for name in inspect.signature(fn).parameters
        if read_schema_markers(hints.get(name, Any))[1]
    )


def _no_default(fn: Callable[..., Any]) -> frozenset[str]:
    """``fn``'s keyword parameters with no default, less those marked ``InputRequired``."""
    hints = callable_type_hints(fn)
    return frozenset(
        name
        for name, parameter in inspect.signature(fn).parameters.items()
        # One arc to coverage, a condition each, held by the cases of
        # test_the_edge_s_variant_relaxes_exactly_the_reads_the_rule_asks_for:
        # the kind by ``typed-dict-key``, the marker by ``input-required``, and
        # the default by test_a_parameter_with_a_default_is_never_named.
        if parameter.kind
        in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
        and parameter.default is inspect.Parameter.empty
        and not read_schema_markers(hints.get(name, Any))[1]
    )


def _owned_keys(callables: Sequence[Callable[..., Any]]) -> frozenset[str]:
    """Every ``NotClientInput`` key of the call: one callable's is the server's for all.

    ``marked_input_keys`` also refuses a misplaced marker, which is how one
    fails at compile, so at registration.
    """
    owned: set[str] = set()
    for fn in callables:
        owned |= marked_input_keys(fn)[1]
    return frozenset(owned)


def _declared(entries: Sequence[UrlKwarg | QueryParam], *, label: str) -> list[Parameter]:
    declared: list[Parameter] = []
    for entry in entries:
        if entry.name in _NOT_ARGUMENTS:
            raise ImproperlyConfigured(
                f"{label}.reads declares {entry.name!r}, which dispatch fills itself from the "
                "pool; an argument of that name would never be the caller's. Rename it."
            )
        declared.append(
            Parameter(
                entry.name,
                entry.type,
                required=bool(getattr(entry, "required", False)),
                default=entry.default,
                help=entry.description,
            )
        )
    return declared


def _refuse_hidden_reads(
    entries: Sequence[UrlKwarg | QueryParam],
    receivers: Sequence[Callable[..., Any]],
    *,
    label: str,
) -> None:
    """Refuse a read that a callable it is handed to marks ``NotClientInput``.

    A declared read is the client's argument, and dispatch hands it by name to
    the spec's service or selector and to each of its preconditions, so one of
    them marking the same name ``NotClientInput`` contradicts itself: the mark
    keeps the name the server's, and the read would fill it from the request.
    A target lookup is not a receiver: it takes a declared read only where its
    own signature reads the name unmarked, so a read named like a key only the
    lookup hides is the service's argument
    (test_a_read_only_a_target_lookup_hides_is_the_service_s,
    test_a_read_named_like_a_hidden_lookup_key_is_the_service_s_argument).

    Each receiver is held by the test that fails without it:
    test_a_service_hiding_a_read_is_refused and
    test_a_service_s_precondition_hiding_a_read_is_refused for a ``ServiceSpec``,
    test_a_selector_hiding_a_read_is_refused and
    test_a_selector_s_precondition_hiding_a_read_is_refused for a ``SelectorSpec``.
    """
    names = {entry.name: entry for entry in entries}
    for fn in receivers:
        for name in sorted(marked_input_keys(fn)[1] & names.keys()):
            entry = names[name]
            owner = getattr(fn, "__qualname__", repr(fn))
            raise ImproperlyConfigured(
                f"{label}.reads declares {type(entry).__name__}({name!r}), which "
                f"{owner} marks NotClientInput. A read is the client's argument and is "
                f"handed to {owner} by that name, while the mark keeps the name the "
                "server's. Remove the read, or the NotClientInput mark."
            )


def _compiled_input_names(spec: Any) -> frozenset[str]:
    """The input serializer's writable names as far as they can be read with no call.

    A spec compiles as it is declared, at import, where no request or view
    exists, so a serializer reading ``self.context["request"]`` or ``["view"]``
    unguarded, as it is built or as its fields are, cannot be described, and
    is taken to declare no name rather than refused: dispatch, which describes
    it with the call's own request and view, is where its fields exist
    (test_a_spec_declaring_a_read_registers_and_mounts_at_import, which fails
    without the catch). A spec registered at import may also compile before
    ``migrate`` has made the table its fields query, or before the row they
    look up exists, so registering never needs the database: a database error
    or a missing row is taken the same way, and the call that describes the
    serializer is where a read named like one of its fields is refused
    (test_registration_needs_no_database). Only those are caught, each held
    by its case of test_a_spec_whose_fields_cannot_be_read_without_a_call_registers
    (``attribute``, ``key``, ``subscript``, ``missing-table``, ``missing-row``);
    anything else the serializer raises is raised as it is
    (test_a_serializer_that_fails_for_another_reason_is_not_hidden_at_registration),
    and so is a declaration that is no serializer at all
    (test_an_input_serializer_that_is_none_of_the_two_is_still_refused).
    """
    try:
        return writable_input_names(spec)
    except UNREADABLE_WITHOUT_A_CALL:
        return frozenset()


def _refuse_reads_named_like_fields(spec: Any, *, lookup_reads: frozenset[str]) -> None:
    """Refuse a read, the spec's or its target lookup's, that a writable input field also takes.

    The body's value of that name is the field's, so a ``QueryParam`` of the
    name reaches nothing: the service's parameter of that name is handed the
    field's value, and a target lookup reading the name takes it from the
    route alone. A ``UrlKwarg`` the target lookup reads is the rename-by-key
    shape, where the route's value scopes the lookup and the body's is the
    field's, so it is accepted (test_a_read_no_field_takes_from_the_body_is_accepted);
    any other ``UrlKwarg`` of the name the kernel would refuse at dispatch as
    declared by two sources, so it is refused here, beside the declaration
    (test_a_read_named_like_a_writable_field_is_refused). A ``many`` spec's
    fields are each row's, and a read-only field and a ``HiddenField`` take
    nothing from the body, so none is a clash (the ``a-rows-field``,
    ``read-only-field`` and ``hidden-field`` cases
    of test_a_read_no_field_takes_from_the_body_is_accepted).
    """
    target = spec.collection_selector_spec or spec.instance_selector_spec
    role = "collection" if spec.collection_selector_spec is not None else "instance"
    sources = [("ServiceSpec", spec.reads or ())]
    if target is not None:
        sources.append((f"ServiceSpec.{role}_selector_spec", target.reads or ()))
    if not any(entries for _, entries in sources):
        # Describing the serializer builds it with no request, so a spec
        # declaring no read is not made to, and one whose fields read the
        # request still registers
        # (test_a_spec_declaring_no_read_registers_without_describing_its_serializer).
        return
    writable = _compiled_input_names(spec)
    for label, entries in sources:
        for entry in entries:
            looked_up = entry.name in lookup_reads
            # One branch to coverage, so each condition is named: without the
            # exemption url-kwarg-the-lookup-reads is refused, without
            # ``looked_up`` url-kwarg-no-lookup-reads is accepted, and without
            # the ``UrlKwarg`` check query-param-the-lookup-reads is accepted.
            if entry.name not in writable or (looked_up and isinstance(entry, UrlKwarg)):
                continue
            lookup = " and the target lookup reads" if looked_up else ""
            raise ImproperlyConfigured(
                f"{label}.reads declares {type(entry).__name__}({entry.name!r}), which the "
                f"input serializer {spec.input_serializer.__qualname__} also takes as a "
                f"writable field{lookup}. "
                "The body's value of that name is the field's, so the read would reach "
                "neither the service nor the lookup. Rename the read or the field; to "
                "scope the target lookup by a key the body also writes, declare it a "
                "UrlKwarg the lookup reads."
            )


def _merge(
    reflected: Sequence[Parameter], declared: Sequence[Parameter], *, skip: frozenset[str]
) -> Parameters:
    """``reflected`` with each declared Parameter in place of its namesake, then the rest declared.

    A declared read says where a value comes from, not that the callable can run
    without one, so the Parameter that takes a reflected one's place is required
    when either is: the read says so (``UrlKwarg(required=True)``) or the
    parameter it names would be with no read declared. One arc to coverage, so
    each operand is named: test_a_read_naming_a_parameter_with_no_default_compiles_required
    fails without ``reflected.required``, and
    test_a_read_saying_required_requires_a_parameter_that_has_a_default without
    ``read.required``. A read declaring a ``default`` stays optional on the
    compiled parameters and the kernel's input schema, which report the default
    and do not fill it in (test_a_read_with_a_default_stays_optional fails
    without that operand), and a
    parameter with a default of its own was never required
    (test_a_parameter_with_a_default_stays_optional_under_a_read).
    """
    by_name = {parameter.name: parameter for parameter in declared}
    merged = [
        _declared_over(by_name.pop(parameter.name, None), parameter) for parameter in reflected
    ]
    merged.extend(by_name.values())
    return Parameters(tuple(parameter for parameter in merged if parameter.name not in skip))


def _required_by(
    callables: Sequence[Callable[..., Any]],
    *,
    hidden: frozenset[str],
    open_ended: bool,
    registry: JsonSchemaRegistry | None,
) -> frozenset[str]:
    """The names a callable of ``callables`` would require with no read declared, as ``_reflected`` asks."""
    return frozenset(
        parameter.name
        for fn in callables
        for parameter in _reflected(fn, hidden=hidden, open_ended=open_ended, registry=registry)
        if parameter.required
    )


def _requiring(declared: Sequence[Parameter], names: frozenset[str]) -> list[Parameter]:
    """``declared`` with each read of a name in ``names`` required, unless it declares a ``default``.

    The reads no reflected Parameter stands beside in ``_merge``: a callable
    that is not the seat's signature (a service under ``AUTO`` or ``BUNDLE``,
    a precondition) still raises without the argument its read fills. One arc
    to coverage, so each conjunct is named:
    test_a_read_naming_a_parameter_with_no_default_compiles_required fails
    without the first, test_the_other_seats_keep_a_default_optional (its
    ``*-read-with-a-default`` cases) without the second, and
    test_a_read_no_callable_takes_by_name_is_not_made_required (with
    test_a_parameter_with_a_default_stays_optional_under_a_read) without the
    name check, which keeps ``names`` the callables' alone.
    """
    return [
        dataclasses.replace(read, required=True)
        if read.name in names and read.default is UNSET
        else read
        for read in declared
    ]


def _declared_over(read: Parameter | None, reflected: Parameter) -> Parameter:
    """``read`` in place of ``reflected``, required where either is; ``reflected`` when none is declared."""
    if read is None:
        return reflected
    required = read.required or (reflected.required and read.default is UNSET)
    return dataclasses.replace(read, required=required)


def _names(parameters: Sequence[Parameter]) -> frozenset[str]:
    return frozenset(parameter.name for parameter in parameters)


def _reflected(
    fn: Callable[..., Any],
    *,
    hidden: frozenset[str],
    open_ended: bool,
    registry: JsonSchemaRegistry | None = None,
) -> list[Parameter]:
    """``fn``'s keyword parameters and ``Unpack``-ed keys, as Parameters, less the pool's and the hidden.

    A named parameter with no default is required: only an argument can fill
    it, and ``fn`` raises ``TypeError`` without one
    (test_a_parameter_with_no_default_is_required), and one with a default is
    not (test_a_parameter_with_a_default_stays_optional). A server's value still
    reaches it - a route capture, a view hook's, a typed provider's, which the
    kernel relaxes, or a registered seed's, which the kernel fills because
    the read is marked ``reflected`` (``TestEachServerFillerAnswers``). A row handed in as ``target=`` does not:
    the kernel checks the arguments before it reads the target, so a caller
    handing one on the kernel's route still sends the lookup's key
    (test_the_kernel_s_route_still_asks_for_the_key). drfs' edge answers it
    without the key, as it always did, because each variant it dispatches
    relaxes what this rule requires (``required_for_having_no_default``,
    test_the_edge_answers_it_without_the_key). It stays optional where the
    kernel cannot see the value coming:

    - ``open_ended``: the call has a ``kwargs`` provider that says nothing
      about its keys (``_filled_by_an_untyped_provider``), which may fill any
      name, so none is asked of the caller, as ``spec_to_json_schema`` asks
      none without ``supplied=``
      (``TestAnUntypedProviderKeepsEveryParameterOptional``);
    - ``fn`` is a wrapper that may fill its own parameters (``fills_its_own``)
      (test_its_parameter_stays_optional_and_the_wrapper_fills_it).

    A positional-only parameter, which ``spec_to_json_schema`` advertises and
    never requires, is no read at all, below. An ``Unpack``-ed key keeps what
    its ``TypedDict`` declares, and ``InputRequired`` stays required either way.
    """
    skip = _NOT_ARGUMENTS | hidden
    # Asked once, as the parameters below are all ``fn``'s.
    asked = not open_ended and not fills_its_own(fn)
    hints = callable_type_hints(fn)
    reflected: list[Parameter] = []
    for name, parameter in inspect.signature(fn).parameters.items():
        if parameter.kind is inspect.Parameter.VAR_KEYWORD:
            typed_dict = unpack_typed_dict(hints.get(name))
            if typed_dict is not None:
                fields, required = typed_dict_input(typed_dict)
                reflected.extend(
                    _parameter(key, hint, required=key in required, registry=registry)
                    for key, hint in fields.items()
                    if key not in skip
                )
            continue
        # Dispatch binds by keyword, so a positional-only parameter is no argument.
        # One arc to coverage: ``test_a_key_any_callable_hides_is_the_server_s_for_the_whole_call``
        # fails without the name check, ``test_unpacked_typed_dict_keys_and_markers``
        # (``*args``) and ``test_a_positional_only_parameter_is_no_read`` without
        # each kind.
        if name in skip or parameter.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.POSITIONAL_ONLY,
        ):
            continue
        empty = parameter.default is inspect.Parameter.empty
        default = UNSET if empty else parameter.default
        reflected.append(
            _parameter(
                name,
                hints.get(name, Any),
                required=empty and asked,
                default=default,
                registry=registry,
            )
        )
    return reflected


def _parameter(
    name: str,
    hint: Any,
    *,
    required: bool,
    default: Any = UNSET,
    registry: JsonSchemaRegistry | None = None,
) -> Parameter:
    underlying, marked_required, _hidden = read_schema_markers(hint)
    kind, nullable, extra = _shape(underlying, registry)
    # An author's ``InputDescription`` is this parameter's, so it wins over a
    # registry rule's ``description`` for its type
    # (test_a_selector_spec_s_reads_take_its_python_type_rules, ``described``).
    registered_help = extra.pop("help", None)
    return Parameter(
        name,
        kind,
        required=required or marked_required,
        nullable=nullable,
        # Every parameter built here is read off a signature, which is what the
        # mark says: the kernel fills a registered seed's name in it on every
        # dispatch, so a selector's ``tenant`` is the project's seed whether the
        # DRF edge or any other transport dispatches it
        # (test_the_kernel_fills_a_selector_s_reflected_read_from_the_seed). A
        # declared ``UrlKwarg`` or ``QueryParam`` is built elsewhere and stays
        # unmarked, so the kernel still refuses one named like a seed
        # (test_a_declared_read_is_not_marked_and_replaces_the_reflected_one).
        reflected=True,
        # ``InputRequired`` is the author saying the caller must send the key, so a
        # Python default beside it (``= None``, written so the signature is valid)
        # is not one the kernel may fill: it lets a required parameter with a
        # default go absent (test_a_none_defaulted_required_key_is_enforced).
        default=UNSET if marked_required else default,
        help=read_input_description(hint) or registered_help,
        **extra,
    )


def _shape(
    annotation: Any, registry: JsonSchemaRegistry | None = None
) -> tuple[str, bool, dict[str, Any]]:
    """``(type, nullable, extra)`` for an annotation; ``any`` for what no Parameter type says.

    A ``registry.python_types`` rule matches by identity, before any built-in
    reading and inside an ``Optional`` or a list, as drfs' annotation walk
    matches it, and its fragment is split by ``fragment_keywords``
    (test_a_selector_spec_s_reads_take_its_python_type_rules).
    """
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is Annotated:
        return _shape(args[0], registry)
    for rule_type, fragment in registry.python_types if registry is not None else ():
        if annotation is rule_type:
            declared = fragment_keywords(fragment, output=False)
            return declared.pop("type"), declared.pop("nullable", False), declared
    if origin in (typing.Union, types.UnionType):
        members = [arg for arg in args if arg is not type(None)]
        nullable = len(members) < len(args)
        if len(members) == 1:
            kind, _, extra = _shape(members[0], registry)
            return kind, nullable, extra
        return "any", nullable, {}
    for python_type, kind, fmt in _SCALARS:
        if annotation is python_type:
            return kind, False, ({"format": fmt} if fmt is not None else {})
    if origin is Literal:
        return _choices(args)
    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        return _choices(tuple(member.value for member in annotation))
    # Each ``or`` below takes a parameterised and a bare spelling; one arc to
    # coverage, so ``test_choices_take_their_values_json_type`` (``list``,
    # ``dict``) and ``test_a_selector_s_named_parameters_are_its_reads``
    # (``list[str]``, ``dict[str, int]``) each hold one side.
    if origin in _ARRAYS or annotation in _ARRAYS:
        element = _shape(args[0], registry)[0] if len(args) == 1 else "any"
        # An array declaring no ``items`` takes any element, which is the one
        # spelling the kernel accepts for that.
        return "array", False, ({"items": element} if element != "any" else {})
    if origin is dict or annotation is dict:
        return "object", False, {}
    # ``Any``, which an unannotated parameter reads as, admits every value,
    # ``None`` among them, as 0.57.0 handed it on
    # (test_a_selector_takes_the_client_s_null). A class no Parameter type
    # names is still no ``None`` (test_a_class_no_parameter_type_names_is_still_no_none).
    return "any", annotation is Any, {}


def _choices(values: tuple[Any, ...]) -> tuple[str, bool, dict[str, Any]]:
    kinds = {_value_type(value) for value in values}
    kind = kinds.pop() if len(kinds) == 1 else None
    if kind is None:
        # Mixed, or of no JSON type: an untyped value is matched by no list.
        return "any", False, {}
    return kind, False, {"choices": values}


def _value_type(value: Any) -> str | None:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    return None


def _list_bounds(spec: Any) -> tuple[int | None, int | None]:
    """A ``many`` spec's list bounds, fewest then most rows, off the list serializer DRF builds for its input.

    DRF decides them in ``many_init`` and ``Meta.list_serializer_class``, so they
    are read off the instance it builds, as drfs' schema reads them; a
    dataclass declares neither hook. ``allow_empty=False`` is a minimum of one.

    Building the list builds the row serializer, so one that cannot be built
    with no call (``UNREADABLE_WITHOUT_A_CALL``) is compiled with no bound of
    its own rather than refused, as ``_compiled_input_names`` takes it: the
    list serializer that validates the call still holds the list to the
    bound it declares, in DRF's words. Each case of
    test_a_many_spec_whose_serializer_cannot_be_built_without_a_call_registers
    fails without the catch, and
    test_registered_before_the_table_exists_its_list_is_still_bounded holds
    the bound.
    """
    serializer = spec.input_serializer
    # One arc to coverage: ``test_a_bounded_serializer_without_many_has_no_list_bounds``
    # fails without ``many``, ``test_many_with_no_serializer_is_the_kernel_s_refusal``
    # without the ``type`` check (``issubclass(None, ...)`` raises first), and
    # ``test_a_dataclass_list_is_unbounded`` without the ``Serializer`` one.
    if not (
        spec.many
        and isinstance(serializer, type)
        and issubclass(serializer, serializers.Serializer)
    ):
        return (None, None)
    try:
        listed: Any = serializer(many=True, context={"request": None, "view": None, "format": None})
    except UNREADABLE_WITHOUT_A_CALL:
        return (None, None)
    minimum = max(listed.min_length or 0, 0 if listed.allow_empty else 1)
    return (minimum or None, listed.max_length)


def _filtering_from_the_call(
    extend: Callable[..., QuerySet[Any]] | None, filter_set: Any
) -> Callable[..., QuerySet[Any]]:
    """An output selector's ``extend_queryset``, then its ``filter_set`` bound to the call's data.

    The data is the call's ``filter_data`` as it was passed - a live view's
    ``QueryDict`` kept one, every key, as ``DjangoFilterBackend`` binds a query
    string - or an empty mapping when the call passed none, never
    ``request.query_params``, since off HTTP there is often no request at all
    (test_explicit_filter_data_still_filters_the_re_read,
    test_filter_data_is_bound_as_it_was_passed,
    test_without_filter_data_the_re_read_binds_an_empty_mapping). The
    ``FilterSet`` is handed ``request`` when its constructor takes one, by the
    kernel's declare-to-receive rule
    (test_a_request_scoped_filter_set_sees_the_call_s_request).

    After ``extend_queryset``, so a filter reads what the callable added
    (test_the_filter_reads_what_extend_queryset_added). Data the ``FilterSet``
    refuses is DRF's ``ValidationError`` built from its errors, a 400 over
    HTTP; the service has run by then, and its write stands
    (test_a_refused_filter_value_leaves_the_write_standing).
    """

    def extend_queryset(queryset: QuerySet[Any], view: Any, request: Any) -> QuerySet[Any]:
        if extend is not None:
            queryset = extend(queryset, view, request)
        data = edge_call_state().filter_data
        bound = filter_set(
            **resolve_callable_kwargs(filter_set, {"request": request}),
            data=data if data is not None else {},
            queryset=queryset,
        )
        if not bound.is_valid():
            raise ValidationError(bound.errors)
        return bound.qs

    return extend_queryset


def _positional(extend: Callable[..., QuerySet[Any]]) -> Callable[..., QuerySet[Any]]:
    """drfs' positional ``extend_queryset`` as a callable the kernel binds by name.

    No ``functools.wraps``: it sets ``__wrapped__``, which ``inspect.signature``
    follows, and the kernel would read the wrapped callable's own names.
    """

    def extend_queryset(queryset: QuerySet[Any], view: Any, request: Any) -> QuerySet[Any]:
        return extend(queryset, view, request)

    return extend_queryset


__all__ = ["as_kernel_spec"]
