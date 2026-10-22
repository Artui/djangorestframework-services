"""A ``JsonSchemaRegistry`` fragment, split onto the kernel's ``Parameter`` and ``OutputField``.

The keywords a field of the declaration spells set that field; the rest is
``json_schema_extra``; and whatever the kernel refuses to build the
declaration with is dropped, the kernel's own refusal being the oracle.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django_service_specs.output.output import OutputField
from django_service_specs.parameters.parameters import Parameter
from django_service_specs.types.utils import (
    OUTPUT_FIELD_SCHEMA_KEYWORDS,
    PARAMETER_SCHEMA_KEYWORDS,
)
from rest_framework import serializers

from rest_framework_services.service_specs.serializer_presenter import _output
from rest_framework_services.service_specs.utils import (
    describe_serializer,
    input_parameters,
)
from rest_framework_services.types.json_schema_registry import JsonSchemaRegistry


class _Registered(serializers.CharField): ...


class _RegisteredRow(serializers.Serializer):
    inner = serializers.IntegerField()


def _registry(fragment: dict[str, Any]) -> JsonSchemaRegistry:
    return JsonSchemaRegistry(fields=((_Registered, fragment), (_RegisteredRow, fragment)))


def _parameter(fragment: dict[str, Any], **field_kwargs: Any) -> Parameter:
    class _In(serializers.Serializer):
        value = _Registered(**field_kwargs)

    (parameter,) = input_parameters(describe_serializer(_In), registry=_registry(fragment))
    return parameter


def _output_field(fragment: dict[str, Any], **field_kwargs: Any) -> OutputField:
    class _Out(serializers.Serializer):
        value = _Registered(**field_kwargs)

    (field,) = _output(describe_serializer(_Out), path=(), registry=_registry(fragment))
    return field


# -- The keywords a field spells set that field --------------------------------


@pytest.mark.parametrize(
    ("fragment", "expected"),
    [
        pytest.param({"type": "integer"}, {"type": "integer"}, id="type"),
        pytest.param(
            {"type": ["integer", "null"]}, {"type": "integer", "nullable": True}, id="nullable-type"
        ),
        pytest.param({"type": "string", "format": "uuid"}, {"format": "uuid"}, id="format"),
        pytest.param({"type": "string", "enum": ["a", "b"]}, {"choices": ("a", "b")}, id="enum"),
        pytest.param({"enum": [1, 2]}, {"type": "integer", "choices": (1, 2)}, id="enum-typed"),
        pytest.param(
            {"type": "number", "enum": [1, 2]},
            {"type": "number", "choices": (1, 2)},
            id="enum-under-its-type",
        ),
        pytest.param(
            {"type": "integer", "minimum": 0, "maximum": 9},
            {"minimum": 0, "maximum": 9},
            id="bounds",
        ),
        pytest.param(
            {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 3},
            {"items": "string", "min_length": 1, "max_length": 3},
            id="items-and-lengths",
        ),
        pytest.param({"type": "string", "description": "Cents."}, {"help": "Cents."}, id="help"),
    ],
)
def test_a_keyword_a_parameter_field_spells_sets_that_field(
    fragment: dict[str, Any], expected: dict[str, Any]
) -> None:
    parameter = _parameter(fragment)
    for name, value in expected.items():
        assert getattr(parameter, name) == value, name
    assert parameter.json_schema_extra is None


@pytest.mark.parametrize(
    ("fragment", "expected"),
    [
        pytest.param({"type": "number"}, {"type": "number"}, id="type"),
        pytest.param({"type": "string", "format": "date"}, {"format": "date"}, id="format"),
        pytest.param(
            {"type": "string", "enum": ["a"]}, {"choices": (("a", "a"),)}, id="enum-as-pairs"
        ),
        pytest.param({"type": "string", "title": "Amount"}, {"label": "Amount"}, id="label"),
        pytest.param(
            {"type": "array", "items": {"type": "integer"}}, {"items": "integer"}, id="items"
        ),
    ],
)
def test_a_keyword_an_output_field_spells_sets_that_field(
    fragment: dict[str, Any], expected: dict[str, Any]
) -> None:
    field = _output_field(fragment)
    for name, value in expected.items():
        assert getattr(field, name) == value, name
    assert field.json_schema_extra is None


# -- The rest is json_schema_extra ---------------------------------------------


def test_a_keyword_no_parameter_field_spells_is_json_schema_extra() -> None:
    rest = {"pattern": "^[0-9]+$", "minLength": 1, "maxLength": 12, "title": "Amount"}
    parameter = _parameter({"type": "string", **rest})
    assert parameter.json_schema_extra == rest


def test_a_keyword_no_output_field_spells_is_json_schema_extra() -> None:
    # An ``OutputField`` has no ``help`` and no bounds, so they stay keywords.
    rest = {"description": "Cents.", "minimum": 0, "pattern": "^[0-9]+$"}
    field = _output_field({"type": "integer", **rest})
    assert field.json_schema_extra == rest


# -- What the kernel refuses in json_schema_extra and no field takes is dropped


def test_a_parameter_keyword_the_kernel_refuses_and_no_field_takes_is_dropped() -> None:
    parameter = _parameter(
        {
            "type": "object",
            "properties": {"a": {}},
            "required": ["a"],
            "additionalProperties": False,
            "anyOf": [{"const": 1}],
            "const": 1,
            "default": {"a": 1},
            "items": {"type": "object", "properties": {}},
        }
    )
    assert parameter.type == "object"
    assert parameter.json_schema_extra is None
    assert parameter.fields is None
    assert parameter.items is None


def test_an_output_keyword_the_kernel_refuses_and_no_field_takes_is_dropped() -> None:
    field = _output_field(
        {"type": "object", "properties": {"a": {}}, "required": ["a"], "oneOf": [], "const": 1}
    )
    assert field.type == "object"
    assert field.json_schema_extra is None
    assert field.fields is None


@pytest.mark.parametrize(
    "fragment",
    [
        pytest.param({"type": ["string", "integer"]}, id="two-types"),
        pytest.param({"type": ["string", "integer", "null"]}, id="two-types-and-null"),
        pytest.param({"enum": [1, "a"]}, id="mixed-enum"),
    ],
)
def test_a_fragment_the_kernel_cannot_type_declares_any(fragment: dict[str, Any]) -> None:
    parameter = _parameter(fragment)
    assert (parameter.type, parameter.nullable, parameter.choices) == ("any", False, None)


@pytest.mark.parametrize(
    "items",
    [
        pytest.param({"type": "string", "minLength": 1}, id="more-than-a-type"),
        pytest.param({"type": "tuple"}, id="no-json-type"),
        # A type the kernel has and JSON Schema does not: the kernel would take it.
        pytest.param({"type": "file"}, id="a-kernel-only-type"),
        pytest.param([{"type": "string"}], id="a-tuple-schema"),
    ],
)
def test_a_fragment_s_items_saying_more_than_a_type_is_dropped(items: Any) -> None:
    parameter = _parameter({"type": "array", "items": items})
    assert (parameter.type, parameter.items, parameter.json_schema_extra) == ("array", None, None)


# -- What the kernel refuses is dropped, and the rest kept -----------------------

_PATTERN = {"pattern": "^[0-9]+$"}


@pytest.mark.parametrize(
    ("output", "fragment", "kept", "dropped"),
    [
        pytest.param(
            False,
            {"format": "date", **_PATTERN},
            {"type": "any", "format": None, "json_schema_extra": _PATTERN},
            {"format": "date"},
            id="format-without-a-type",
        ),
        pytest.param(
            False,
            {"type": "string", "minimum": 1, **_PATTERN},
            {"minimum": None, "json_schema_extra": _PATTERN},
            {"minimum": 1},
            id="minimum-on-a-string",
        ),
        pytest.param(
            False,
            {"type": "string", "minItems": 1, **_PATTERN},
            {"min_length": None, "json_schema_extra": _PATTERN},
            {"min_length": 1},
            id="lengths-on-a-string",
        ),
        pytest.param(
            False,
            {"type": "string", "items": {"type": "string"}, **_PATTERN},
            {"items": None, "json_schema_extra": _PATTERN},
            {"items": "string"},
            id="items-on-a-string",
        ),
        pytest.param(
            False,
            {"type": "integer", "minimum": 5, "maximum": 1},
            {"minimum": 5, "maximum": None},
            {"minimum": 5, "maximum": 1},
            id="bounds-crossed",
        ),
        pytest.param(
            False,
            {"type": "array", "minItems": -1, "maxItems": 3},
            {"min_length": None, "max_length": 3},
            {"min_length": -1},
            id="one-length-refused-beside-a-kept-one",
        ),
        pytest.param(
            False,
            {"type": "string", "x-scale": Decimal("0.01"), **_PATTERN},
            {"json_schema_extra": _PATTERN},
            {"json_schema_extra": {"x-scale": Decimal("0.01")}},
            id="kept-beside-a-refused-one",
        ),
        pytest.param(
            True,
            {"format": "date", "title": "Paid on", **_PATTERN},
            {"type": "any", "format": None, "label": "Paid on", "json_schema_extra": _PATTERN},
            {"format": "date"},
            id="output-format-without-a-type",
        ),
        pytest.param(
            True,
            {"type": "integer", "enum": [1, 2], "x-scale": Decimal("1"), **_PATTERN},
            {"choices": ((1, "1"), (2, "2")), "json_schema_extra": _PATTERN},
            {"json_schema_extra": {"x-scale": Decimal("1")}},
            id="output-kept-beside-a-refused-one",
        ),
    ],
)
def test_what_the_kernel_refuses_is_dropped_and_the_rest_kept(
    output: bool, fragment: dict[str, Any], kept: dict[str, Any], dropped: dict[str, Any]
) -> None:
    declared = _output_field(fragment) if output else _parameter(fragment)
    assert {name: getattr(declared, name) for name in kept} == kept
    # The oracle: what was dropped is what the kernel refuses beside the rest.
    declaration = OutputField if output else Parameter
    with pytest.raises(ImproperlyConfigured):
        declaration("value", declared.type, **dropped)


def test_a_bound_the_format_makes_legal_is_kept() -> None:
    # Each keyword is asked about beside the ones kept before it: ``minimum``
    # on a string is refused unless the string is a decimal.
    parameter = _parameter({"type": "string", "format": "decimal", "minimum": 1})
    assert (parameter.format, parameter.minimum) == ("decimal", 1)


def test_a_format_that_is_no_name_is_dropped() -> None:
    parameter = _parameter({"type": "string", "format": ["date"], **_PATTERN})
    assert (parameter.format, parameter.json_schema_extra) == (None, _PATTERN)


# -- A format outside the kernel's four ------------------------------------------


def test_a_format_outside_the_kernel_s_four_is_dropped() -> None:
    parameter = _parameter({"type": "string", "format": "money"})
    assert (parameter.type, parameter.format, parameter.json_schema_extra) == ("string", None, None)
    field = _output_field({"type": "string", "format": "money"})
    assert (field.type, field.format, field.json_schema_extra) == ("string", None, None)


# -- The field's own declaration ---------------------------------------------------


def test_the_field_s_own_help_label_and_null_stand_beside_the_fragment() -> None:
    fragment = {"type": "string", "description": "Registered.", "title": "Registered"}
    parameter = _parameter(fragment, help_text="Own.", allow_null=True, required=False)
    assert (parameter.help, parameter.nullable, parameter.required) == ("Own.", True, False)
    field = _output_field(fragment, label="Own", allow_null=True)
    assert (field.label, field.nullable) == ("Own", True)


def test_a_rule_wins_over_the_walk_of_a_nested_serializer() -> None:
    class _In(serializers.Serializer):
        row = _RegisteredRow()
        rows = _RegisteredRow(many=True)

    registry = JsonSchemaRegistry(fields=((_RegisteredRow, {"type": "object"}),))
    row, rows = input_parameters(describe_serializer(_In), registry=registry)
    assert (row.type, row.fields) == ("object", None)
    # ``many=True`` wraps the registered class in a ``ListSerializer``, which is
    # matched by its own class, so the rows are walked, as drfs walks them.
    assert rows.type == "array"
    assert not isinstance(rows.items, str)
    one, many = _output(describe_serializer(_In), path=(), registry=registry)
    assert (one.type, one.fields) == ("object", None)
    assert many.type == "array"
    assert not isinstance(many.items, str)


def test_a_registered_list_element_is_typed_by_its_rule() -> None:
    class _In(serializers.Serializer):
        values = serializers.ListField(child=_Registered())

    class _Out(serializers.Serializer):
        values = serializers.ListField(child=_Registered())

    registry = _registry({"type": "integer", "format": "money"})
    (parameter,) = input_parameters(describe_serializer(_In), registry=registry)
    assert (parameter.items, parameter.format) == ("integer", None)
    (field,) = _output(describe_serializer(_Out), path=(), registry=registry)
    assert field.items == "integer"


def test_a_nested_serializer_s_fields_are_matched_too() -> None:
    class _Inner(serializers.Serializer):
        value = _Registered()

    class _In(serializers.Serializer):
        inner = _Inner()
        rows = _Inner(many=True)
        listed = serializers.ListField(child=_Inner())

    registry = _registry({"type": "integer"})
    walked = input_parameters(describe_serializer(_In), registry=registry)
    for parameter in walked:
        rows = parameter.fields if parameter.fields is not None else parameter.items
        assert not isinstance(rows, str)
        assert rows is not None
        assert rows.get("value").type == "integer", parameter.name
    output = _output(describe_serializer(_In), path=(), registry=registry)
    assert output.fields[0].fields is not None
    assert output.fields[0].fields.fields[0].type == "integer"


def test_without_a_registry_the_walk_is_unchanged() -> None:
    class _In(serializers.Serializer):
        value = _Registered(max_length=4)

    assert input_parameters(describe_serializer(_In)) == input_parameters(
        describe_serializer(_In), registry=None
    )


# -- The split covers the kernel's tables --------------------------------------------


def test_every_keyword_the_kernel_refuses_is_set_or_dropped() -> None:
    """Each keyword the kernel's tables refuse in ``json_schema_extra`` is handled.

    Set where a field spells it, else dropped, whatever the tables hold the
    day a kernel release adds to them, so the split never hands the kernel a
    keyword it refuses.
    """
    parameter = _parameter(dict.fromkeys(PARAMETER_SCHEMA_KEYWORDS))
    field = _output_field(dict.fromkeys(OUTPUT_FIELD_SCHEMA_KEYWORDS))
    assert (parameter.type, parameter.json_schema_extra) == ("any", None)
    assert (field.type, field.json_schema_extra) == ("any", None)
