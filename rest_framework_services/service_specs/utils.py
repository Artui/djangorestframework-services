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
from collections.abc import Sequence
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django_service_specs.parameters.parameters import Parameter, Parameters
from django_service_specs.types.unset import UNSET as KERNEL_UNSET
from rest_framework import ISO_8601, relations, serializers
from rest_framework.settings import api_settings
from rest_framework_dataclasses.serializers import DataclassSerializer

from rest_framework_services.dispatch.base_serializer_context import base_serializer_context
from rest_framework_services.types.unset import UNSET

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
