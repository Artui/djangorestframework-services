"""The invariant the whole feature rests on: the two sides agree.

Each of these renders a payload and generates a schema from the *same*
declaration, then validates one against the other with a real JSON Schema
validator. A round trip through our own helpers only proves they agree with
each other.
"""

from __future__ import annotations

from typing import Any

import pytest
from jsonschema import Draft202012Validator
from rest_framework import serializers

from rest_framework_services.audience.build_audience_projection import build_audience_projection
from rest_framework_services.audience.project_payload import project_payload
from rest_framework_services.jsonschema.output_to_json_schema import output_to_json_schema
from rest_framework_services.jsonschema.utils import field_to_schema
from rest_framework_services.types.field_marking import MARKING, FieldMarking
from rest_framework_services.types.selector_kind import SelectorKind

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
