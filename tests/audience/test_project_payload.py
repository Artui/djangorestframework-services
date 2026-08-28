"""Tests for shaping a rendered payload to an agent audience."""

from __future__ import annotations

from rest_framework import serializers

from rest_framework_services.audience.build_audience_projection import build_audience_projection
from rest_framework_services.audience.project_payload import project_payload
from rest_framework_services.types.field_marking import MARKING, FieldMarking

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
