"""Tests for shaping a rendered payload to an agent audience."""

from __future__ import annotations

from django.utils import timezone
from rest_framework import serializers

from rest_framework_services.audience.build_audience_projection import build_audience_projection
from rest_framework_services.audience.project_payload import project_payload
from rest_framework_services.types.field_audience import FieldAudience
from rest_framework_services.types.field_marking import MARKING, FieldMarking
from rest_framework_services.types.value_formatter import ValueFormatter

STATUSES = [("PENDING_REVIEW", "Awaiting review"), ("PAID", "Paid")]


class _Line(serializers.Serializer):
    sku = serializers.CharField()
    internal = serializers.CharField(style={MARKING: FieldMarking.hidden()})


class _Invoice(serializers.Serializer):
    id = serializers.UUIDField(style={MARKING: FieldMarking.handle()})
    etag = serializers.CharField(style={MARKING: FieldMarking.hidden()})
    number = serializers.CharField(style={MARKING: FieldMarking.label()})
    status = serializers.ChoiceField(choices=STATUSES)
    kind = serializers.ChoiceField(choices=STATUSES, style={MARKING: FieldMarking.handle()})
    lines = _Line(many=True)


PAYLOAD = {
    "id": "f47ac10b",
    "etag": 'W/"3a"',
    "number": "FV/2026/0043",
    "status": "PENDING_REVIEW",
    "kind": "PENDING_REVIEW",
    "lines": [{"sku": "A-1", "internal": "x"}],
}


def test_drops_hidden_and_speaks_labels() -> None:
    projected = project_payload(PAYLOAD, build_audience_projection(_Invoice))

    assert "etag" not in projected
    assert projected["status"] == "Awaiting review"
    # A handle is somebody else's input, so its constant survives verbatim.
    assert projected["kind"] == "PENDING_REVIEW"
    assert projected["id"] == "f47ac10b"
    assert projected["lines"] == [{"sku": "A-1"}]


def test_projects_a_list_of_records() -> None:
    projected = project_payload([PAYLOAD, PAYLOAD], build_audience_projection(_Invoice))

    assert len(projected) == 2
    assert all("etag" not in row for row in projected)


def test_unknown_choice_value_passes_through() -> None:
    projection = build_audience_projection(_Invoice)
    projected = project_payload({**PAYLOAD, "status": "ARCHIVED"}, projection)

    assert projected["status"] == "ARCHIVED"


def test_empty_projection_returns_payload_unchanged() -> None:
    class _Plain(serializers.Serializer):
        name = serializers.CharField()

    payload = {"name": "x"}
    assert project_payload(payload, build_audience_projection(_Plain)) is payload


def test_non_mapping_payload_passes_through() -> None:
    projection = build_audience_projection(_Invoice)

    assert project_payload("just a string", projection) == "just a string"
    assert project_payload(None, projection) is None


class _Formatted(serializers.Serializer):
    """Every collision the formatter path has to decide, in one declaration."""

    due_at = serializers.DateTimeField(style={MARKING: FieldMarking.timestamp("%Y-%m-%d %H:%M")})
    closed_at = serializers.DateTimeField(
        allow_null=True, style={MARKING: FieldMarking.timestamp()}
    )
    # A choice the author has also given a formatter: two transforms, one field.
    status = serializers.ChoiceField(
        choices=STATUSES,
        style={MARKING: FieldMarking.formatted(ValueFormatter(str.title, "string"))},
    )
    # A handle that is *also* formatted, which the handle wins.
    id = serializers.CharField(
        style={
            MARKING: FieldMarking(
                FieldAudience.HANDLE, formatter=ValueFormatter(str.upper, "string")
            )
        }
    )


FORMATTED_PAYLOAD = {
    "due_at": "2026-01-31T12:00:00Z",
    "closed_at": None,
    "status": "PENDING_REVIEW",
    "id": "inv-7",
}


def test_formats_a_marked_value_in_the_active_timezone() -> None:
    with timezone.override("UTC"):
        projected = project_payload(FORMATTED_PAYLOAD, build_audience_projection(_Formatted))

    assert projected["due_at"] == "2026-01-31 12:00"


def test_a_null_is_left_alone_rather_than_formatted() -> None:
    projected = project_payload(FORMATTED_PAYLOAD, build_audience_projection(_Formatted))

    assert projected["closed_at"] is None


def test_an_explicit_formatter_beats_the_derived_choice_substitution() -> None:
    """Declared beats derived, deliberately — not by whichever branch came first."""
    projected = project_payload(FORMATTED_PAYLOAD, build_audience_projection(_Formatted))

    assert projected["status"] == "Pending_Review"


def test_a_handle_is_never_formatted() -> None:
    """A formatted machine identifier is a broken one."""
    projected = project_payload(FORMATTED_PAYLOAD, build_audience_projection(_Formatted))

    assert projected["id"] == "inv-7"


def test_a_formatter_on_a_nested_field_is_still_the_one_transform_that_applies() -> None:
    """It is an odd thing to declare, but it is explicit, so it is honoured."""

    class _Parent(serializers.Serializer):
        lines = _Line(
            many=True, style={MARKING: FieldMarking.formatted(ValueFormatter(len, "integer"))}
        )

    projected = project_payload(
        {"lines": [{"sku": "A-1", "internal": "x"}]}, build_audience_projection(_Parent)
    )

    assert projected == {"lines": 1}
