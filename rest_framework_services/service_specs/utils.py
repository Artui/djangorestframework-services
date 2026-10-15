"""The serializer field walk the DRF adapter reads a serializer through.

A DRF serializer declares what it takes and what it renders in one set of
fields, so both directions are read here: what one field is, as a JSON type,
format, choices and labels, is the same whichever way it faces, and is read by
the direction-neutral helpers (``field_type``, ``field_choices``). Which fields
take part is not, so the input walk (``input_parameters``) selects its own: a
``HiddenField`` and a read-only field are no parameter, since DRF ignores what
a caller sends for either.

**The shape check runs in front of DRF**, so a type declared here is a type
the kernel refuses a call for before the serializer sees it. The rule
throughout is therefore that a declaration may be incomplete and never
stricter than the field: a field with no single JSON type of its own - a
``JSONField``, a related field, which DRF reads from a string as readily as
from a number, or a field this walk does not know - declares ``any``, which
the shape check passes on as it came. It is the same policy drfs' own input
schema follows, which writes ``{}`` for each of them.

What a field means is read as drfs' ``serializer_to_json_schema`` reads it, so
the two describe one serializer alike; the parity test names each place the
two dialects differ and why.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django_service_specs.parameters.parameters import Parameter, Parameters
from django_service_specs.pool.resolve_callable_kwargs import resolve_callable_kwargs
from django_service_specs.types.unset import UNSET as KERNEL_UNSET
from rest_framework import ISO_8601, relations, serializers
from rest_framework.settings import api_settings
from rest_framework_dataclasses.serializers import DataclassSerializer

from rest_framework_services.service_specs.base_serializer_context import base_serializer_context
from rest_framework_services.service_specs.build_offline_context import build_offline_context
from rest_framework_services.types.unset import UNSET
from rest_framework_services.types.view_hooks import ViewHooks

MAX_SERIALIZER_APPEARANCES = 4
"""How many times one serializer class may appear on the walk's current path
before the next appearance is truncated to an open object.

drfs' own input schema's allowance, and for the same reason: a
self-referential serializer is an ordinary shape and must not raise
``RecursionError`` while a transport describes it, and a serializer bounding
its own depth with a countdown is usually written three levels below its root,
so four appearances describe it as written. Kept equal to drfs' so the two
truncate one declaration at the same node."""

_SCALARS: tuple[tuple[type[serializers.Field], str, str | None], ...] = (
    (serializers.BooleanField, "boolean", None),
    (serializers.IntegerField, "integer", None),
    (serializers.FloatField, "number", None),
    # A decimal is a string in the decimal format, which the shape check also
    # takes as a JSON number, as DRF's field does.
    (serializers.DecimalField, "string", "decimal"),
    (serializers.DateTimeField, "string", "date-time"),
    (serializers.DateField, "string", "date"),
    # Each parses a string, in a format the kernel has no name for.
    (serializers.TimeField, "string", None),
    (serializers.DurationField, "string", None),
    (serializers.UUIDField, "string", None),
    # Email, URL, Slug, Regex and IPAddress all subclass CharField.
    (serializers.CharField, "string", None),
    # An open mapping: no ``fields``, so the shape check takes any key. HStore
    # subclasses DictField.
    (serializers.DictField, "object", None),
)
"""The fields with one JSON type of their own, and its format. DRF's scalar
fields do not subclass one another across rows, so the order matters only
within the CharField family, which ends the string rows."""


def describe_serializer(declaration: Any) -> serializers.Serializer:
    """An unbound serializer for ``declaration``, built to be described.

    ``declaration`` is what a drfs spec's ``input_serializer`` takes: a
    ``Serializer`` subclass, or a dataclass, which is read through a
    ``DataclassSerializer`` exactly as dispatch validates it. Anything else is
    refused with ``ImproperlyConfigured``.

    It is built with the context DRF builds on HTTP, with no view and no
    request, because a description is made before any call exists: a
    ``get_fields`` reading ``self.context["request"]`` finds the key and reads
    ``None``, rather than raising ``KeyError`` from a schema while dispatch
    would have rendered it.
    """
    return bind_serializer(declaration, context=base_serializer_context(view=None, request=None))


def bind_serializer(declaration: Any, **kwargs: Any) -> serializers.Serializer:
    """``declaration`` built as a serializer with ``kwargs``, or ``ImproperlyConfigured``."""
    if isinstance(declaration, type) and issubclass(declaration, serializers.Serializer):
        return declaration(**kwargs)
    if isinstance(declaration, type) and dataclasses.is_dataclass(declaration):
        return DataclassSerializer(dataclass=declaration, **kwargs)
    raise ImproperlyConfigured(
        f"{declaration!r} cannot be read as a serializer: declare a Serializer subclass or a "
        "dataclass, as an input_serializer takes."
    )


SERIALIZER_CONTEXT_SEED = "_serializer_context_layers"
"""The key a DRF edge adds to a result's presentation context for the view's output layers.

Only ever a key of the ``ambient`` of a ``DispatchResult``'s
``PresentationContext``, added by the edge once the kernel has returned, never
a pool seed: the presenter runs when the transport renders, after the call, so
the view's ``output_serializer_context`` hook - lazy, handed the value
presented - travels with the result rather than through the call's pool, where
a callable taking a bare ``**kwargs`` would be handed it
(test_a_bare_kwargs_service_sees_no_edge_machinery)."""


@dataclasses.dataclass
class EdgeCallState:
    """What one call of the DRF edge carries for its own machinery, beside the kernel's pool.

    Everything here is the edge's, never an argument or a seed of the call, so
    it does not travel through the pool the kernel hands a callable taking a
    bare ``**kwargs``, which on main held ``progress``, ``request``,
    ``serializer``, ``user`` and the call's extras and nothing of the edge's
    (test_a_bare_kwargs_service_sees_no_edge_machinery). The edge sets it in
    ``EDGE_CALL`` around the whole call, and the compiled pieces that need it
    read it from there:

    - ``target_guard``: a selector spec's guard, run by the precondition the
      edge prepends (``guarded_selector``).
    - ``author_values``: a view hook's and the spec's ``kwargs`` provider's
      values naming no argument, which the compiled spec's ``kwargs`` seat
      (``author_values``) hands back over the Validator's values.
    - ``view_hooks``: the view's serializer context layers, whose input half
      the Validator reads (``input_view_layers``).
    - ``validator_input``: the Validator the edge dispatches, and the body it
      binds rather than the kernel's checked arguments (``validator_input``):
      the caller's own, a ``QueryDict`` kept one, with what the edge put over
      it, as DRF's serializer has always been handed the request's data.
    - ``body_rows``: whether that body is a live view's list body, which the
      Validator binds whole, every row as sent, as DRF's view bound it
      (``SerializerValidator.validated_many``).
    - ``row_errors``: what the list serializer bound to that whole body
      refused, as the installed DRF reports it, which the edge answers with
      in place of the kernel's rows (``edge_errors``).

    Mutable, so the edge can set it before the sync step it hops to a thread
    for and fill it there, and the Validator can fill it during the kernel's
    call: the thread runs in a copy of the context that holds this same object.
    """

    target_guard: Callable[[Any], None] | None = None
    author_values: Mapping[str, Any] = dataclasses.field(default_factory=dict)
    view_hooks: ViewHooks = dataclasses.field(default_factory=ViewHooks)
    validator_input: tuple[Any, Any] | None = None
    body_rows: bool = False
    row_errors: Any = None


EDGE_CALL: ContextVar[EdgeCallState | None] = ContextVar(
    "rest_framework_services_edge_call", default=None
)
"""The DRF edge's state for the call in progress, or ``None`` outside one.

A context variable rather than a pool seed, so nothing the edge carries for
itself reaches a spec's callables. The kernel's sync steps run in this
context, and ``adispatch``'s thread hops run in a copy of it, which
``asgiref`` makes for every ``sync_to_async`` call."""


def edge_call_state() -> EdgeCallState:
    """The state of the edge call in progress, or an empty one outside any call."""
    return EDGE_CALL.get() or EdgeCallState()


def validator_input(validator: Any, arguments: Any) -> Any:
    """What ``validator`` binds for the call: the edge's body for it, else ``arguments``.

    Only the Validator the edge call names reads the edge's body, so a kernel
    call made inside the service, of another spec or of this one, binds its
    own arguments (test_a_kernel_call_inside_the_service_binds_its_own_arguments).
    """
    held = edge_call_state().validator_input
    return held[1] if held is not None and held[0] is validator else arguments


def author_values() -> dict[str, Any]:
    """The author values a DRF edge carries for the call, as a ``kwargs`` provider answers.

    The compiled form of a service spec the edge dispatches carries this as its
    ``kwargs`` provider (``kernel_spec_variant(server_input=False)``), since the
    edge calls the spec's own provider itself. Empty outside an edge call, and
    for a call that carries none.
    """
    return dict(edge_call_state().author_values)


def input_view_layers() -> Mapping[str, Any] | None:
    """The input half of the view hooks the edge call carries, or ``None``."""
    return edge_call_state().view_hooks.input_serializer_context


def output_view_layers(ambient: Mapping[str, Any], value: Any) -> Mapping[str, Any] | None:
    """The output half of the view hooks the call registered, resolved for ``value``, or ``None``.

    Lazy on the carrier, because a view's output hook may declare the
    ``result`` it renders, which exists only once the call has run. One branch
    to coverage, so each condition is named by the test that fails without it:
    test_the_output_callable_reaches_the_output_serializer (no hooks
    registered, a call that reached no edge) and
    test_an_input_half_alone_reaches_the_input_serializer (hooks registered
    with no output half).
    """
    hooks: ViewHooks | None = ambient.get(SERIALIZER_CONTEXT_SEED)
    if hooks is None or hooks.output_serializer_context is None:
        return None
    return hooks.output_serializer_context(value)


def serializer_context(
    principal: Any,
    ambient: Mapping[str, Any],
    arguments: Mapping[str, Any] | list[Any] | None,
    *,
    view_layers: Mapping[str, Any] | None = None,
    provider: Callable[..., Mapping[str, Any]] | None = None,
    extras: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The serializer context a call's serializer is built under: four layers, later wins.

    1. **DRF's own.** The ``request`` and ``view`` the call registered are the
       serializer's own, as they are on HTTP, so a field or a ``validate()``
       reading either finds what the transport put there; a registered view
       that answers ``get_serializer_context`` supplies the whole context, as
       DRF's views do. **Only when the call registered neither** is an offline
       pair built, with ``principal`` as the request's ``user`` and
       ``arguments`` as its data, so a ``CurrentUserDefault`` reads the
       principal and a serializer reading ``self.context["request"]`` finds a
       request off HTTP too. One seed alone is kept as it is, beside a ``None``
       for the other: pairing a registered request with a view built around
       another one would describe a call that never happened.
    2. and 3. **The view's hooks**, ``view_layers``: the directional and
       per-action hooks the view resolved, one direction's half of what the
       edge carries: the input half from the call's ``EdgeCallState``, the
       output half from the result's presentation context, under
       ``SERIALIZER_CONTEXT_SEED``.
    4. **The spec's own callable**, ``provider``, which has the final say. It
       is called through the kernel's declared-kwargs resolver, as every other
       spec callable is, from a pool of the call's ``arguments`` (a mapping's
       entries; a ``many`` call's rows are no names, held by
       test_a_many_spec_s_rows_validate_under_the_input_callable_once), beneath its ``ambient``
       seeds, beneath the ``request`` and ``view`` of layer 1 and ``user`` (the
       principal), beneath ``extras`` (the value an output callable renders).
       A client's argument never stands in for one of the call's own objects
       (test_an_argument_never_stands_in_for_the_request_the_callable_declares).
       A key it answers with ``UNSET`` is declined, leaving the layer beneath,
       as on every transport drfs has (test_an_unset_value_declines_the_key_and_keeps_the_layer_beneath).

    One branch to coverage for the offline pair: the request seed alone is held
    by test_a_registered_request_alone_is_kept_beside_no_view, the view seed
    alone by test_a_registered_view_alone_answers_for_the_serializer_context,
    and neither by test_the_serializer_context_carries_an_offline_request_for_the_principal.
    """
    request = ambient.get("request")
    view = ambient.get("view")
    if request is None and view is None:
        offline = build_offline_context(principal, arguments)
        request, view = offline.request, offline.view
    context = base_serializer_context(view=view, request=request)
    context.update(view_layers or {})
    if provider is not None:
        pool: dict[str, Any] = {
            **(arguments if isinstance(arguments, Mapping) else {}),
            # The edge's key for the output layers is no seed of the call, and
            # stays out of a callable's pool as every seed-free value does
            # (test_a_bare_kwargs_service_sees_no_edge_machinery).
            **{name: value for name, value in ambient.items() if name != SERIALIZER_CONTEXT_SEED},
            "request": request,
            "view": view,
            "user": principal,
            **(extras or {}),
        }
        resolved = provider(**resolve_callable_kwargs(provider, pool))
        context.update({key: value for key, value in resolved.items() if value is not UNSET})
    return context


def is_input(field: serializers.Field) -> bool:
    """Whether a caller's value for ``field`` reaches the serializer at all.

    A read-only field is rendered and never read, and a ``HiddenField`` reads
    only its default, whatever a caller sends; so neither is a parameter, and
    under the kernel's closed argument set a caller sending either is refused
    rather than silently ignored. One branch to coverage, so each condition
    is held by its own field of
    test_a_hidden_field_and_every_read_only_field_are_not_parameters: ``who``
    (the ``HiddenField``) and the read-only rest.
    """
    return not (field.read_only or isinstance(field, serializers.HiddenField))


def input_parameters(
    serializer: serializers.Serializer, path: tuple[type[Any], ...] = ()
) -> Parameters:
    """What ``serializer`` takes, as Parameters, in field order. Never queries.

    ``path`` carries the serializer classes being described, innermost last,
    which is how a recursive declaration is bounded: see
    ``MAX_SERIALIZER_APPEARANCES``. A caller leaves it empty.
    """
    path = (*path, type(serializer))
    return Parameters(
        tuple(
            input_parameter(name, field, path)
            for name, field in serializer.fields.items()
            if is_input(field)
        )
    )


def input_parameter(name: str, field: serializers.Field, path: tuple[type[Any], ...]) -> Parameter:
    """One input field as a Parameter, named for the wire.

    ``required`` is the field's own, which DRF already reads from a default
    and from ``read_only``; ``nullable`` is its ``allow_null``; ``help`` its
    ``help_text``, rendered as a string in the language active now. A default
    is reported only when it is a literal (see ``literal_default``).
    """
    common: dict[str, Any] = {
        "required": field.required,
        "nullable": field.allow_null,
        "default": literal_default(field),
        "help": str(field.help_text) if field.help_text else None,
    }
    if isinstance(field, serializers.ListSerializer):
        # ``many=True`` on a nested serializer: an array of rows. The wrapper is
        # no serializer level of its own, so it costs no appearance.
        return Parameter(
            name,
            "array",
            items=_rows(field.child, path),
            **_lengths(field),
            **common,
        )
    if isinstance(field, serializers.Serializer):
        if _truncated(field, path):
            return Parameter(name, "object", **common)
        return Parameter(name, "object", fields=input_parameters(field, path), **common)
    # One branch to coverage, each class held by its own tests:
    # test_a_list_field_declares_its_element_and_its_length and
    # test_a_multiple_choice_field_is_an_array_whose_elements_are_the_choices.
    if isinstance(field, serializers.ListField | serializers.MultipleChoiceField):
        return Parameter(name, "array", **_element(field, path), **_lengths(field), **common)
    if isinstance(field, relations.ManyRelatedField):
        # A list of keys, each read as its related field reads one: from a
        # string as readily as from a number, so the element declares no type.
        return Parameter(name, "array", **common)
    json_type, fmt = input_type(field)
    choices = field_choices(field)
    if choices is None:
        return Parameter(name, json_type, format=fmt, **common)
    if choices.type is None:
        # No one JSON type for the values, and ``any`` lists no choices.
        return Parameter(name, "any", **common)
    return Parameter(
        name,
        choices.type,
        choices=choices.values,
        choice_labels=choices.labels,
        **common,
    )


def field_type(field: serializers.Field) -> tuple[str, str | None]:
    """The JSON type and format of one value ``field`` takes or renders.

    Direction-neutral. A file is a ``file``; a field with no single JSON type
    of its own - a ``JSONField``, a related field, a ``ModelField``, a custom
    field - is ``any``, so nothing is claimed that the field does not hold to.
    A choice field's type is its values', read by ``field_choices``.
    """
    if isinstance(field, serializers.FileField):
        # ImageField included.
        return "file", None
    for cls, json_type, fmt in _SCALARS:
        if isinstance(field, cls):
            return json_type, fmt
    return "any", None


def input_type(field: serializers.Field) -> tuple[str, str | None]:
    """The JSON type and format of one value ``field`` takes, for the shape check in front of it.

    ``field_type``, refined by what only the input side reads, so that the
    declaration is never stricter than the field:

    - a date or date-time read in any format but ISO 8601, its own or the
      project's ``DATE_INPUT_FORMATS`` / ``DATETIME_INPUT_FORMATS``, declares
      no format, since the kernel's names for the two are ISO's and would
      refuse ``14/10/2026`` before a field reading ``%d/%m/%Y`` saw it;
    - a localized decimal declares no type, since it reads the separators of
      the language active for the call, ``1,5`` in German, as well as a JSON
      number.
    """
    json_type, fmt = field_type(field)
    formats = _input_formats(field)
    # One branch to coverage: without the ``None`` test every other field
    # raises TypeError, and without the formats test
    # test_each_field_declares_its_json_type_and_format[date] declares no format for an ISO date.
    if formats is not None and any(each != ISO_8601 for each in formats):
        return json_type, None
    # One branch to coverage: without the isinstance every field but a
    # decimal raises AttributeError, and test_each_field_declares_its_json_type_and_format[decimal]
    # holds ``localize``.
    if isinstance(field, serializers.DecimalField) and field.localize:
        return "any", None
    return json_type, fmt


def _input_formats(field: serializers.Field) -> Sequence[str] | None:
    """The formats a date or date-time field reads, as DRF reads them when it validates.

    The field's own ``input_formats``, else the project's setting, read now
    rather than when the field was built, as DRF reads it. ``None`` for any
    other field.
    """
    # DateTimeField does not subclass DateField, so the order is free.
    if isinstance(field, serializers.DateTimeField):
        return getattr(field, "input_formats", api_settings.DATETIME_INPUT_FORMATS)
    if isinstance(field, serializers.DateField):
        return getattr(field, "input_formats", api_settings.DATE_INPUT_FORMATS)
    return None


@dataclasses.dataclass(frozen=True)
class FieldChoices:
    """A choice field's values, their shared JSON type, and their labels.

    ``type`` is ``None`` when the values share no single JSON type, which a
    declaration cannot list choices for. ``labels`` are the display DRF holds
    for each value, unrendered, so a lazy translation is rendered when a schema
    is built.
    """

    type: str | None
    values: tuple[Any, ...]
    labels: tuple[tuple[Any, Any], ...]


def field_choices(field: serializers.Field) -> FieldChoices | None:
    """The choices ``field`` takes, or ``None`` for a field that is no choice field.

    DRF's ``choices`` are already flat, option groups included, keyed by the
    value as declared. A ``ChoiceField`` that allows blank also takes ``""``,
    which its choices need not list; it is added, since a declaration of
    choices claims every value accepted, and the shape check would otherwise
    refuse the blank DRF takes. ``None`` under ``allow_null`` is the
    parameter's ``nullable``, not a choice.

    DRF matches a value by its ``str``, so it also takes ``"1"`` for the choice
    ``1``; the declaration states the type the values are written in, as drfs'
    own schema does.
    """
    if not isinstance(field, serializers.ChoiceField):
        return None
    return _choices_of(field)


def _choices_of(field: serializers.ChoiceField) -> FieldChoices:
    """``field_choices`` for a field known to be a choice field."""
    labels = tuple(field.choices.items())
    values = tuple(value for value, _label in labels)
    # One branch to coverage: test_allow_blank_adds_the_empty_string_to_string_choices
    # holds the flag, and test_allow_blank_does_not_list_the_empty_string_twice
    # the membership, without which a blank choice is listed twice.
    if getattr(field, "allow_blank", False) and "" not in values:
        values = (*values, "")
    found = {_json_type(value) for value in values} or {"string"}
    # ``None`` in the set is a value of no JSON scalar type, such as a
    # ``None`` choice itself; with it, as with two types, no single type holds.
    shared = found.pop() if len(found) == 1 else None
    return FieldChoices(type=shared, values=values, labels=labels)


def literal_default(field: serializers.Field) -> Any:
    """``field``'s default as a declaration a transport could print, or the kernel's ``UNSET``.

    A callable default - a function, ``CurrentUserDefault``,
    ``CreateOnlyDefault`` - is computed per call, so no one value is its
    default. DRF's ``empty`` and drfs' ``UNSET`` both mean no default; ``empty``
    is a class, so it is callable, and the one test answers for it too
    (test_a_required_field_is_required_and_takes_no_null). One branch to
    coverage, so each condition is held by its own test:
    test_the_unset_sentinel_as_a_default_is_no_default (``UNSET``) and
    test_a_callable_default_is_no_literal_default (the callable, ``empty``
    among them).
    """
    default: Any = field.default
    if default is UNSET or callable(default):
        return KERNEL_UNSET
    return default


def _json_type(value: Any) -> str | None:
    """The JSON type of a scalar value, or ``None`` for anything else.

    ``bool`` first: it subclasses ``int``, and ``true`` is not an integer on
    any wire.
    """
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    return None


def _truncated(serializer: serializers.Serializer, path: tuple[type[Any], ...]) -> bool:
    return path.count(type(serializer)) >= MAX_SERIALIZER_APPEARANCES


def _rows(child: Any, path: tuple[type[Any], ...]) -> Parameters | str:
    """A list's rows: the child serializer's Parameters, or an open object past the bound."""
    return "object" if _truncated(child, path) else input_parameters(child, path)


def _element(
    field: serializers.ListField | serializers.MultipleChoiceField, path: tuple[type[Any], ...]
) -> dict[str, Any]:
    """What a list's element declares, as the array's own keywords.

    An array's ``items`` holds the element's JSON type, or its Parameters for
    a row; its format and choices describe each element and so sit on the
    array. Only a decimal's format is one an element takes: a list of dates
    is a list of strings, which the field decodes.
    """
    if isinstance(field, serializers.MultipleChoiceField):
        own = _choices_of(field)
        if own.type is None:
            return {}
        return {"items": own.type, "choices": own.values, "choice_labels": own.labels}
    child: Any = field.child
    if isinstance(child, serializers.Serializer):
        return {"items": _rows(child, path)}
    # One branch to coverage, each class held by its own case of
    # test_a_list_field_s_element_type: [list] and [list-of-rows].
    if isinstance(child, serializers.ListField | serializers.ListSerializer):
        return {"items": "array"}
    choices = field_choices(child)
    # One branch to coverage: test_a_list_of_choices_declares_them_on_the_array
    # holds the first condition, and test_a_list_of_mixed_choices_declares_none
    # the second, without which a list of mixed choices lists them on an
    # untyped array, and the shape check refuses the ``"1"`` DRF matches to ``1``.
    if choices is not None and choices.type is not None:
        return {
            "items": choices.type,
            "items_nullable": child.allow_null,
            "choices": choices.values,
            "choice_labels": choices.labels,
        }
    json_type, fmt = input_type(child)
    if json_type == "any":
        # An element of no single type, a choice field's of mixed values among
        # them: an array that declares no ``items`` takes any element already.
        return {}
    return {
        "items": json_type,
        "items_nullable": child.allow_null,
        "format": fmt if fmt == "decimal" else None,
    }


def _lengths(field: Any) -> dict[str, Any]:
    """A list's ``min_length`` and ``max_length``, with ``allow_empty=False`` a minimum of one.

    Declared so the shape check holds a list to what DRF holds it to; a list
    declaring no maximum is held to the kernel's own default by the shape
    check, which drfs' schema never stated.
    """
    minimum: int | None = getattr(field, "min_length", None)
    if not field.allow_empty:
        minimum = max(minimum or 0, 1)
    return {"min_length": minimum or None, "max_length": getattr(field, "max_length", None)}
