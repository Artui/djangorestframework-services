"""``as_kernel_spec`` - a drfs ``ServiceSpec`` or ``SelectorSpec`` compiled to the kernel's."""

from __future__ import annotations

import datetime
import decimal
import enum
import inspect
import types
import typing
from collections.abc import Callable, Iterator, Mapping, Sequence
from typing import Annotated, Any, Literal, cast, get_args, get_origin

from django.core.exceptions import ImproperlyConfigured
from django.db.models import QuerySet
from django_service_specs.adapters.filters.filter_set_parameters import filter_set_parameters
from django_service_specs.authorization.permission_check import PermissionCheck
from django_service_specs.authorization.unrestricted import Unrestricted
from django_service_specs.parameters.parameters import Parameter, Parameters
from django_service_specs.specs.kernel_spec import kernel_spec
from django_service_specs.specs.selector_spec import SelectorSpec as KernelSelectorSpec
from django_service_specs.specs.service_spec import ServiceSpec as KernelServiceSpec
from rest_framework import serializers

from rest_framework_services.service_specs.drf_permission_check import DRFPermissionCheck
from rest_framework_services.service_specs.serializer_presenter import SerializerPresenter
from rest_framework_services.service_specs.serializer_validator import SerializerValidator
from rest_framework_services.types.affordance_declarer import AffordanceDeclarer
from rest_framework_services.types.field_marking import FieldMarking
from rest_framework_services.types.marked_input_keys import marked_input_keys
from rest_framework_services.types.query_param import QueryParam
from rest_framework_services.types.read_input_description import read_input_description
from rest_framework_services.types.read_schema_markers import read_schema_markers
from rest_framework_services.types.reserved_pool_seeds import RESERVED_POOL_SEEDS
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.typed_dict_input import typed_dict_input
from rest_framework_services.types.unpack_typed_dict import unpack_typed_dict
from rest_framework_services.types.unset import UNSET
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
    spec: Any, *, partial: bool | None = None
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
      compiled spec's own presenter.
    - ``permission_classes`` becomes one
      [`DRFPermissionCheck`][rest_framework_services.service_specs.drf_permission_check.DRFPermissionCheck];
      an empty sequence, which drfs reads as "no permissions, explicitly",
      becomes ``[Unrestricted()]``, the kernel's spelling of the same; ``None``
      compiles to no permissions, which the kernel's registry and dispatch
      refuse, so a drfs spec reaches a transport without a view only guarded.
    - ``reads`` is what the selector, the target lookup and a service with no
      ``input_serializer`` take, read off their signatures as
      [`spec_to_json_schema`][rest_framework_services.jsonschema.spec_to_json_schema.spec_to_json_schema]
      reads them: named parameters and the keys of an ``Unpack``-ed
      ``TypedDict``, with ``InputRequired`` read as required and a
      ``NotClientInput`` key on any callable of the call left out. A misplaced
      marker is refused here, so at registration. Each declared ``UrlKwarg``
      and ``QueryParam`` in the spec's own ``reads`` is a Parameter too,
      winning over a reflected one of its name; one the target lookup reads
      joins the lookup's reads. A name the ``filter_set`` reads is the filter's,
      as the filter field always won the schema. The pool's own names
      (``user``, ``request``, ``view``, ``serializer`` and the kernel's
      reserved seeds) are never arguments, and a declared one is refused. A
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

    **What the kernel refuses at construction is stripped or refused here**,
    naming the drfs field. Stripped, because drfs never read them: an output
    selector's ``kwargs``, an instance lookup's ``allow_none`` (the kernel would
    resolve a missing row to ``None``, an upsert, where drfs answers 404), a
    nested spec's ``permission_classes`` and a target lookup's
    ``output_serializer``. Refused, as drfs refuses them at ``as_view()``: a
    nested selector's ``preconditions`` and ``progress_reporter``, which would
    never run. Also refused: a ``selector`` of ``None`` anywhere but an output
    selector, and ``field_audiences`` with no output serializer to layer them
    over.

    ``success_status``, ``document_service_error`` and ``response_finalizer``
    are the HTTP views', and ``input_serializer_context`` and
    ``output_serializer_context`` have no seat on the adapters yet; none of
    them is carried.
    """
    if isinstance(spec, AffordanceDeclarer):
        return _service(spec, partial=partial)
    return _selector(spec, role=None, hidden=frozenset(), declared=())


def _service(spec: Any, *, partial: bool | None) -> KernelServiceSpec:
    hidden = _owned_keys(
        [spec.service, *(spec.preconditions or ()), *_lookup_selectors(spec)],
    )
    declared = _declared(spec.reads, label="ServiceSpec")
    instance = _nested(spec, "instance", hidden, declared)
    collection = _nested(spec, "collection", hidden, declared)
    target = instance or collection
    # In the lookup's order: the route a permission class reads is filled in it.
    taken = tuple(parameter.name for parameter in target.reads) if target is not None else ()
    reads = _merge(
        _reflected(spec.service, hidden=hidden) if _takes_its_signature(spec) else [],
        [parameter for parameter in declared if parameter.name not in taken],
        skip=frozenset(taken),
    )
    output, presenter = _output(spec)
    compiled_partial = (spec.partial is True) if partial is None else partial
    return KernelServiceSpec(
        service=spec.service,
        permissions=_permissions(spec, route_reads=taken),
        reads=reads,
        partial=compiled_partial and spec.input_serializer is not None,
        many=spec.many,
        many_argument=spec.many_argument,
        **_list_bounds(spec),
        validator=(
            SerializerValidator(spec.input_serializer)
            if spec.input_serializer is not None
            else None
        ),
        instance_selector_spec=instance,
        collection_selector_spec=collection,
        output_selector_spec=output,
        presenter=presenter,
        allow_none=spec.allow_none,
        affordances=spec.affordances,
        atomic=spec.atomic,
        idempotent=spec.idempotent,
        preconditions=spec.preconditions,
        kwargs=spec.kwargs,
        input_data=spec.input_data,
        progress_reporter=spec.progress_reporter,
        metadata=spec.metadata,
    )


def _selector(
    spec: Any,
    *,
    role: str | None,
    hidden: frozenset[str],
    declared: Sequence[Parameter],
    audiences: Mapping[str, FieldMarking] | None = None,
) -> KernelSelectorSpec:
    """A top-level selector spec (``role`` ``None``), or a service's nested one by its role.

    A nested lookup takes the declared Parameters naming what its selector
    reads; the rest are the service's. An output selector takes none: it
    re-reads the service's result, with no caller's arguments to read.
    """
    label = "SelectorSpec" if role is None else f"ServiceSpec.{role}_selector_spec"
    if role is not None:
        _refuse_nested_runs(spec, label)
    if spec.selector is None:
        raise ImproperlyConfigured(
            f"{label} declares no selector, and only an output selector may: there is "
            "nothing else to run. Declare the selector that reads the rows."
        )
    if role is None:
        hidden = _owned_keys([spec.selector, *(spec.preconditions or ())])
        declared = _declared(spec.reads, label=label)
        audiences = spec.field_audiences
    reads = Parameters()
    if role != "output":
        reflected = _reflected(spec.selector, hidden=hidden)
        if role is not None:
            declared = [p for p in declared if p.name in _names(reflected)]
        # A name the ``filter_set`` reads is the filter's: the kernel adds the
        # filter's Parameter itself, and refuses the same name from two sources.
        filtered = (
            filter_set_parameters(spec.filter_set).names()
            if spec.filter_set is not None
            else frozenset()
        )
        reads = _merge(reflected, declared, skip=filtered)
    return KernelSelectorSpec(
        kind=spec.kind,
        selector=spec.selector,
        # A nested selector's checks are never read: the dispatched spec's are.
        permissions=(
            _permissions(spec, route_reads=_route_reads(spec, reads)) if role is None else None
        ),
        reads=reads,
        # A target lookup's rows are the service's to consume, never presented.
        presenter=(
            _presenter(spec.output_serializer, audiences, label=label)
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
        kwargs=spec.kwargs if role != "output" else None,
        preconditions=spec.preconditions,
        progress_reporter=spec.progress_reporter,
        metadata=spec.metadata,
    )


def _nested(
    spec: Any, role: str, hidden: frozenset[str], declared: Sequence[Parameter]
) -> KernelSelectorSpec | None:
    nested = getattr(spec, f"{role}_selector_spec")
    if nested is None:
        return None
    return _selector(nested, role=role, hidden=hidden, declared=declared)


def _output(spec: Any) -> tuple[KernelSelectorSpec | None, SerializerPresenter | None]:
    """The compiled output selector, or the presenter a selector-less one moves onto the service."""
    output = spec.output_selector_spec
    if output is None:
        _presenter(None, spec.field_audiences, label="ServiceSpec")
        return None, None
    audiences = _audiences(output, spec.field_audiences)
    if output.selector is not None:
        return _selector(
            output, role="output", hidden=frozenset(), declared=(), audiences=audiences
        ), None
    _refuse_nested_runs(output, "ServiceSpec.output_selector_spec")
    return None, _presenter(output.output_serializer, audiences, label="ServiceSpec")


def _presenter(
    serializer: Any, audiences: Mapping[str, FieldMarking] | None, *, label: str
) -> SerializerPresenter | None:
    if serializer is None:
        if audiences:
            raise ImproperlyConfigured(
                f"{label} declares field_audiences and no output serializer. "
                "field_audiences layers markings over the output serializer's fields, and "
                "with none there is nothing to mark; declare the output serializer, or "
                "remove field_audiences."
            )
        return None
    return SerializerPresenter(serializer, field_audiences=audiences)


def _audiences(
    output: Any, outer: Mapping[str, FieldMarking] | None
) -> Mapping[str, FieldMarking] | None:
    # The service spec's declaration over its output selector's own, since the
    # service spec is the operation a mount names.
    merged = {**(output.field_audiences or {}), **(outer or {})}
    return merged or None


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


def _takes_its_signature(spec: Any) -> bool:
    """Whether a service's own parameters are its caller's arguments.

    Only with no ``input_serializer``, which otherwise declares the input, as
    ``spec_to_json_schema`` lists them. A ``many`` spec always has one: the
    kernel refuses ``many`` with no validator.
    """
    return spec.input_serializer is None


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


def _merge(
    reflected: Sequence[Parameter], declared: Sequence[Parameter], *, skip: frozenset[str]
) -> Parameters:
    """``reflected`` with each declared Parameter in place of its namesake, then the rest declared."""
    by_name = {parameter.name: parameter for parameter in declared}
    merged = [by_name.pop(parameter.name, parameter) for parameter in reflected]
    merged.extend(by_name.values())
    return Parameters(tuple(parameter for parameter in merged if parameter.name not in skip))


def _names(parameters: Sequence[Parameter]) -> frozenset[str]:
    return frozenset(parameter.name for parameter in parameters)


def _reflected(fn: Callable[..., Any], *, hidden: frozenset[str]) -> list[Parameter]:
    """``fn``'s keyword parameters and ``Unpack``-ed keys, as Parameters, less the pool's and the hidden."""
    skip = _NOT_ARGUMENTS | hidden
    hints = callable_type_hints(fn)
    reflected: list[Parameter] = []
    for name, parameter in inspect.signature(fn).parameters.items():
        if parameter.kind is inspect.Parameter.VAR_KEYWORD:
            typed_dict = unpack_typed_dict(hints.get(name))
            if typed_dict is not None:
                fields, required = typed_dict_input(typed_dict)
                reflected.extend(
                    _parameter(key, hint, required=key in required)
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
        default = UNSET if parameter.default is inspect.Parameter.empty else parameter.default
        reflected.append(_parameter(name, hints.get(name, Any), required=False, default=default))
    return reflected


def _parameter(name: str, hint: Any, *, required: bool, default: Any = UNSET) -> Parameter:
    underlying, marked_required, _hidden = read_schema_markers(hint)
    kind, nullable, extra = _shape(underlying)
    return Parameter(
        name,
        kind,
        required=required or marked_required,
        nullable=nullable,
        default=default,
        help=read_input_description(hint),
        **extra,
    )


def _shape(annotation: Any) -> tuple[str, bool, dict[str, Any]]:
    """``(type, nullable, extra)`` for an annotation; ``any`` for what no Parameter type says."""
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is Annotated:
        return _shape(args[0])
    if origin in (typing.Union, types.UnionType):
        members = [arg for arg in args if arg is not type(None)]
        nullable = len(members) < len(args)
        if len(members) == 1:
            kind, _, extra = _shape(members[0])
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
        element = _shape(args[0])[0] if len(args) == 1 else "any"
        # An array declaring no ``items`` takes any element, which is the one
        # spelling the kernel accepts for that.
        return "array", False, ({"items": element} if element != "any" else {})
    if origin is dict or annotation is dict:
        return "object", False, {}
    return "any", False, {}


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


def _list_bounds(spec: Any) -> dict[str, int | None]:
    """A ``many`` spec's list bounds, off the list serializer DRF builds for its input.

    DRF decides them in ``many_init`` and ``Meta.list_serializer_class``, so they
    are read off the instance it builds, as drfs' schema reads them; a
    dataclass declares neither hook. ``allow_empty=False`` is a minimum of one.
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
        return {}
    listed: Any = serializer(many=True, context={"request": None, "view": None, "format": None})
    minimum = max(listed.min_length or 0, 0 if listed.allow_empty else 1)
    return {"many_min_length": minimum or None, "many_max_length": listed.max_length}


def _positional(extend: Callable[..., QuerySet[Any]]) -> Callable[..., QuerySet[Any]]:
    """drfs' positional ``extend_queryset`` as a callable the kernel binds by name.

    No ``functools.wraps``: it sets ``__wrapped__``, which ``inspect.signature``
    follows, and the kernel would read the wrapped callable's own names.
    """

    def extend_queryset(queryset: QuerySet[Any], view: Any, request: Any) -> QuerySet[Any]:
        return extend(queryset, view, request)

    return extend_queryset


__all__ = ["as_kernel_spec"]
