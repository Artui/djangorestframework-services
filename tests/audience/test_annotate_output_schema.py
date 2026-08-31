"""Tests for mirroring an agent projection onto a JSON Schema."""

from __future__ import annotations

from rest_framework import serializers

from rest_framework_services.audience.annotate_output_schema import annotate_output_schema
from rest_framework_services.audience.build_audience_projection import build_audience_projection
from rest_framework_services.jsonschema.output_to_json_schema import output_to_json_schema
from rest_framework_services.types.field_audience import FieldAudience
from rest_framework_services.types.field_marking import MARKING, FieldMarking
from rest_framework_services.types.json_schema_registry import DEFAULT_JSON_SCHEMA_REGISTRY
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.value_formatter import ValueFormatter


class _Line(serializers.Serializer):
    sku = serializers.CharField()
    internal = serializers.CharField(style={MARKING: FieldMarking.hidden()})


class _Invoice(serializers.Serializer):
    id = serializers.UUIDField(style={MARKING: FieldMarking.handle("Invoice handle.")})
    customer = serializers.IntegerField(style={MARKING: FieldMarking.handle()})
    etag = serializers.CharField(style={MARKING: FieldMarking.hidden()})
    number = serializers.CharField(
        help_text="Invoice number.", style={MARKING: FieldMarking.label()}
    )
    lines = _Line(many=True)


HANDLE_WORDING = "Opaque identifier. Pass it on; do not read it out."


def _annotated(**kwargs: object) -> dict:
    schema = output_to_json_schema(_Invoice, **kwargs)  # type: ignore[arg-type]
    return annotate_output_schema(
        schema, build_audience_projection(_Invoice), handle_description=HANDLE_WORDING
    )


def test_hidden_properties_leave_schema_and_required() -> None:
    schema = _annotated()

    assert "etag" not in schema["properties"]
    assert "etag" not in schema["required"]
    assert "id" in schema["properties"]


def test_handle_descriptions() -> None:
    properties = _annotated()["properties"]

    assert properties["id"]["description"] == "Invoice handle."
    # A handle with no declared wording falls back to the caller's sentence.
    assert properties["customer"]["description"] == HANDLE_WORDING
    # help_text survives where nothing overrides it.
    assert properties["number"]["description"] == "Invoice number."


def test_nested_serializer_is_annotated_through_its_array() -> None:
    items = _annotated()["properties"]["lines"]["items"]

    assert "internal" not in items["properties"]
    assert "sku" in items["properties"]


def test_list_schema_wraps_the_annotated_item() -> None:
    schema = _annotated(kind=SelectorKind.LIST)

    assert schema["type"] == "array"
    assert "etag" not in schema["items"]["properties"]


def test_required_is_dropped_when_everything_is_hidden() -> None:
    class _AllHidden(serializers.Serializer):
        a = serializers.CharField(style={MARKING: FieldMarking.hidden()})

    schema = annotate_output_schema(
        output_to_json_schema(_AllHidden), build_audience_projection(_AllHidden)
    )

    assert schema["properties"] == {}
    assert "required" not in schema


def test_none_and_empty_projection_pass_through() -> None:
    class _Plain(serializers.Serializer):
        name = serializers.CharField()

    empty = build_audience_projection(_Plain)
    assert annotate_output_schema(None, empty) is None

    schema = output_to_json_schema(_Plain)
    assert annotate_output_schema(schema, empty) is schema


def test_schema_without_properties_is_returned_as_is() -> None:
    projection = build_audience_projection(_Invoice)
    schema = {"type": "string"}

    assert annotate_output_schema(schema, projection) == {"type": "string"}


class TestSpokenChoiceSchemas:
    """A substituted choice must be *described* in the values it now carries."""

    def test_a_labelled_choice_is_redeclared_in_display_values(self) -> None:
        class _Order(serializers.Serializer):
            status = serializers.ChoiceField(
                choices=[("PENDING_REVIEW", "Awaiting review"), ("PAID", "Paid")]
            )

        projection = build_audience_projection(_Order)
        schema = annotate_output_schema(output_to_json_schema(_Order), projection)

        assert schema["properties"]["status"] == {
            "oneOf": [{"const": "Awaiting review"}, {"const": "Paid"}]
        }

    def test_a_handle_keeps_its_constants(self) -> None:
        class _Order(serializers.Serializer):
            kind = serializers.ChoiceField(
                choices=[("PENDING_REVIEW", "Awaiting review")],
                style={MARKING: FieldMarking.handle()},
            )

        projection = build_audience_projection(_Order)
        schema = annotate_output_schema(output_to_json_schema(_Order), projection)

        assert schema["properties"]["kind"]["oneOf"][0]["const"] == "PENDING_REVIEW"

    def test_a_registry_rule_is_rewritten_or_left_alone(self) -> None:
        """A consumer rule replaces the fragment, and both shapes are handled."""

        class _Listed(serializers.ChoiceField): ...

        class _Opaque(serializers.ChoiceField): ...

        class _Order(serializers.Serializer):
            listed = _Listed(choices=[("PENDING_REVIEW", "Awaiting review"), ("PAID", "Paid")])
            opaque = _Opaque(choices=[("PAID", "Paid")])

        registry = DEFAULT_JSON_SCHEMA_REGISTRY.extend(
            fields=[
                (_Listed, {"enum": ["PENDING_REVIEW", "PAID"]}),
                (_Opaque, {"type": "string"}),
            ]
        )
        projection = build_audience_projection(_Order)
        schema = output_to_json_schema(_Order, registry=registry)
        properties = annotate_output_schema(schema, projection)["properties"]

        assert properties["listed"] == {"enum": ["Awaiting review", "Paid"]}
        # Nothing enum-shaped to rewrite; the rule is respected as written.
        assert properties["opaque"] == {"type": "string"}


def test_an_unlabelled_handle_says_nothing_by_default() -> None:
    """What a reader should *do* with an identifier depends on the reader.

    This package does not know which kind is reading, so it supplies no wording
    unless the transport that does know passes one in.
    """
    schema = annotate_output_schema(
        output_to_json_schema(_Invoice), build_audience_projection(_Invoice)
    )

    assert "description" not in schema["properties"]["customer"]
    # An explicitly declared description is still emitted; it came from the author.
    assert schema["properties"]["id"]["description"] == "Invoice handle."


class _Formatted(serializers.Serializer):
    """The schema mirror of every collision ``project_payload`` has to decide."""

    due_at = serializers.DateTimeField(
        label="Payment due",
        help_text="When payment is due.",
        style={MARKING: FieldMarking.timestamp()},
    )
    # Nothing to carry across: no author label, no help_text.
    seen_at = serializers.DateTimeField(style={MARKING: FieldMarking.timestamp()})
    status = serializers.ChoiceField(
        choices=[("PENDING_REVIEW", "Awaiting review")],
        style={MARKING: FieldMarking.formatted(ValueFormatter(str.title, "string"))},
    )
    id = serializers.DateTimeField(
        style={
            MARKING: FieldMarking(
                FieldAudience.HANDLE, formatter=ValueFormatter(str.upper, "string")
            )
        }
    )
    amount = serializers.IntegerField(
        style={
            MARKING: FieldMarking.formatted(
                ValueFormatter(lambda cents: f"EUR {cents / 100:.2f}", "string"),
                "The invoice total.",
            )
        }
    )


def _formatted_properties() -> dict:
    projection = build_audience_projection(_Formatted)
    return annotate_output_schema(output_to_json_schema(_Formatted), projection)["properties"]


def test_a_formatted_property_is_redeclared_as_what_it_produces() -> None:
    """``format: date-time`` described the raw value, and that value is gone."""
    seen_at = _formatted_properties()["seen_at"]

    assert seen_at == {"type": "string", "examples": ["31 Jan 2026 14:05"]}


def test_a_formatted_property_keeps_what_annotates_the_field() -> None:
    """``title`` and ``help_text`` describe the field, not the value's shape."""
    due_at = _formatted_properties()["due_at"]

    assert due_at["title"] == "Payment due"
    assert due_at["description"] == "When payment is due."
    assert due_at["type"] == "string"
    assert "format" not in due_at


def test_the_markings_description_still_wins_over_everything() -> None:
    assert _formatted_properties()["amount"]["description"] == "The invoice total."


def test_a_formatter_replaces_the_choice_declaration_it_beats() -> None:
    """The payload no longer carries constants, so the schema must not list them."""
    status = _formatted_properties()["status"]

    assert status == {"type": "string"}


def test_a_handle_is_not_reformatted_in_the_schema_either() -> None:
    """Suppressed on both sides from one place, so the two cannot diverge."""
    assert _formatted_properties()["id"] == {"type": "string", "format": "date-time"}
