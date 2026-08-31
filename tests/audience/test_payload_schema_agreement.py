"""The invariant the whole feature rests on: the two sides agree.

Each of these renders a payload and generates a schema from the *same*
declaration, then validates one against the other with a real JSON Schema
validator. A round trip through our own helpers only proves they agree with
each other.
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest
from django.utils import timezone
from jsonschema import Draft202012Validator
from rest_framework import serializers

from rest_framework_services.audience.build_audience_projection import build_audience_projection
from rest_framework_services.audience.project_payload import project_payload
from rest_framework_services.jsonschema.output_to_json_schema import output_to_json_schema
from rest_framework_services.jsonschema.utils import field_to_schema
from rest_framework_services.types.field_marking import MARKING, FieldMarking
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.value_formatter import ValueFormatter

STATUSES = [("PENDING_REVIEW", "Awaiting review"), ("PAID", "Paid")]


class _Line(serializers.Serializer):
    sku = serializers.CharField()
    internal_cost = serializers.CharField(style={MARKING: FieldMarking.hidden()})


class _Invoice(serializers.Serializer):
    id = serializers.IntegerField(style={MARKING: FieldMarking.handle()})
    number = serializers.CharField(style={MARKING: FieldMarking.label()})
    etag = serializers.CharField(style={MARKING: FieldMarking.hidden()})
    status = serializers.ChoiceField(choices=STATUSES)
    kind = serializers.ChoiceField(choices=STATUSES, style={MARKING: FieldMarking.handle()})
    tags = serializers.MultipleChoiceField(choices=STATUSES)
    lines = _Line(many=True)
    extra_lines = serializers.ListField(child=_Line())


ROW: dict[str, Any] = {
    "id": 7,
    "number": "INV-1",
    "etag": "W/1",
    "status": "PENDING_REVIEW",
    "kind": "PAID",
    "tags": ["PENDING_REVIEW", "PAID"],
    "lines": [{"sku": "S1", "internal_cost": "9.99"}],
    "extra_lines": [{"sku": "S2", "internal_cost": "1.00"}],
}
PROJECTION = build_audience_projection(_Invoice)


def _assert_agrees(payload: Any, schema: Any) -> None:
    Draft202012Validator.check_schema(schema)
    errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=str)
    assert not errors, [error.message for error in errors]


def test_retrieve_payload_validates_against_its_schema() -> None:
    payload = project_payload(ROW, PROJECTION)
    _assert_agrees(payload, output_to_json_schema(_Invoice, projection=PROJECTION))

    assert "etag" not in payload
    assert payload["status"] == "Awaiting review"
    # A handle is another tool's input, so its constant is never re-spelled.
    assert payload["kind"] == "PAID"
    # A MultipleChoiceField renders a collection; each member is substituted.
    assert payload["tags"] == ["Awaiting review", "Paid"]
    # Both ways of nesting a child serializer are projected.
    assert payload["lines"] == [{"sku": "S1"}]
    assert payload["extra_lines"] == [{"sku": "S2"}]


def test_list_payload_validates_against_its_schema() -> None:
    payload = project_payload([ROW, ROW], PROJECTION)
    schema = output_to_json_schema(_Invoice, kind=SelectorKind.LIST, projection=PROJECTION)

    _assert_agrees(payload, schema)


def test_paginated_payload_validates_against_its_envelope() -> None:
    """The envelope's keys belong to no serializer, so the item is the target."""
    payload = {
        "items": project_payload([ROW], PROJECTION),
        "page": 1,
        "totalPages": 1,
        "hasNext": False,
    }
    schema: Any = output_to_json_schema(
        _Invoice, kind=SelectorKind.LIST, paginate=True, projection=PROJECTION
    )

    _assert_agrees(payload, schema)
    assert "etag" not in schema["properties"]["items"]["items"]["properties"]


def test_a_field_drf_may_omit_is_not_claimed_as_required() -> None:
    """``Field.get_attribute`` raises ``SkipField`` and the key never appears."""

    class _Partial(serializers.Serializer):
        always = serializers.CharField()
        sometimes = serializers.CharField(required=False)

    payload = dict(_Partial(instance={"always": "x"}).data)
    schema = output_to_json_schema(_Partial)

    assert payload == {"always": "x"}
    _assert_agrees(payload, schema)


@pytest.mark.parametrize(
    "field",
    [
        serializers.ChoiceField(choices=[(None, "Unknown"), ("a", "Alpha")], allow_null=True),
        serializers.ChoiceField(choices=[("", "Any"), ("a", "Alpha")], allow_blank=True),
    ],
    ids=["null-already-a-choice", "blank-already-a-choice"],
)
def test_widening_never_duplicates_a_declared_choice(field: serializers.ChoiceField) -> None:
    """A second ``const`` for the same value makes ``oneOf`` match twice, and fail."""
    schema = {"type": "object", "properties": {"value": field_to_schema(field)}}
    accepted = "" if field.allow_blank else None

    assert field.run_validation(accepted) == accepted
    _assert_agrees({"value": accepted}, schema)


class _Formatted(serializers.Serializer):
    """Three formatters, one of which changes the JSON type outright."""

    due_at = serializers.DateTimeField(style={MARKING: FieldMarking.timestamp()})
    amount_cents = serializers.IntegerField(
        style={
            MARKING: FieldMarking.formatted(
                ValueFormatter(
                    lambda cents: f"EUR {cents / 100:.2f}",
                    "string",
                    {"examples": ["EUR 12.40"]},
                )
            )
        }
    )
    status = serializers.ChoiceField(
        choices=STATUSES,
        style={MARKING: FieldMarking.formatted(ValueFormatter(str.title, "string"))},
    )


FORMATTED_PROJECTION = build_audience_projection(_Formatted)


def test_a_formatted_payload_validates_against_its_schema() -> None:
    """The declaration moves both sides at once, or it moves neither honestly.

    ``amount_cents`` is the case that cannot pass by luck: the walk describes an
    ``integer`` and the formatter renders a string, so a schema that did not
    follow the payload is rejected by a real validator rather than merely
    looking stale.

    The payload is what DRF renders, not a string typed out here, so the
    date-time reaching the formatter is the one a transport would actually get.
    """
    instance = {
        "due_at": datetime.datetime(2026, 1, 31, 12, 0, tzinfo=datetime.timezone.utc),
        "amount_cents": 1240,
        "status": "PAID",
    }

    with timezone.override("UTC"):
        payload = project_payload(dict(_Formatted(instance=instance).data), FORMATTED_PROJECTION)

    schema = output_to_json_schema(_Formatted, projection=FORMATTED_PROJECTION)
    _assert_agrees(payload, schema)

    assert payload == {
        "due_at": "31 Jan 2026 12:00",
        "amount_cents": "EUR 12.40",
        "status": "Paid",
    }


def test_the_advertised_shape_is_the_shape_that_was_rendered() -> None:
    """An example that drifts from the format is the schema lying quietly."""
    properties: Any = output_to_json_schema(_Formatted, projection=FORMATTED_PROJECTION)[
        "properties"
    ]

    assert properties["due_at"] == {"type": "string", "examples": ["31 Jan 2026 14:05"]}
    assert properties["amount_cents"] == {"type": "string", "examples": ["EUR 12.40"]}
    # The choice constants are gone from both sides, not just from the payload.
    assert properties["status"] == {"type": "string"}


def test_an_unmarked_serializer_is_untouched_on_both_sides() -> None:
    """Unset is today's behaviour: the fast path out, and an identical schema."""

    class _Plain(serializers.Serializer):
        name = serializers.CharField()
        due_at = serializers.DateTimeField()

    projection = build_audience_projection(_Plain)
    payload = {"name": "x", "due_at": "2026-01-31T12:00:00Z"}

    assert projection.is_empty()
    assert project_payload(payload, projection) is payload
    assert output_to_json_schema(_Plain, projection=projection) == output_to_json_schema(_Plain)


def test_a_marked_but_unformatted_declaration_never_reaches_the_new_path() -> None:
    """Every existing assertion in this suite runs on this projection, unchanged."""
    assert all(PROJECTION.formatter(name) is None for name in PROJECTION.fields)
