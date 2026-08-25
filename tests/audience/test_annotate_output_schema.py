"""Tests for mirroring an agent projection onto a JSON Schema."""

from __future__ import annotations

from rest_framework import serializers

from rest_framework_services.audience.annotate_output_schema import (
    HANDLE_DESCRIPTION,
    annotate_output_schema,
)
from rest_framework_services.audience.build_agent_projection import build_agent_projection
from rest_framework_services.jsonschema.output_to_json_schema import output_to_json_schema
from rest_framework_services.types.agent_field import AGENT, AgentField
from rest_framework_services.types.json_schema_registry import DEFAULT_JSON_SCHEMA_REGISTRY
from rest_framework_services.types.selector_kind import SelectorKind


class _Line(serializers.Serializer):
    sku = serializers.CharField()
    internal = serializers.CharField(style={AGENT: AgentField.hidden()})


class _Invoice(serializers.Serializer):
    id = serializers.UUIDField(style={AGENT: AgentField.handle("Invoice handle.")})
    customer = serializers.IntegerField(style={AGENT: AgentField.handle()})
    etag = serializers.CharField(style={AGENT: AgentField.hidden()})
    number = serializers.CharField(help_text="Invoice number.", style={AGENT: AgentField.label()})
    lines = _Line(many=True)


def _annotated(**kwargs: object) -> dict:
    schema = output_to_json_schema(_Invoice, **kwargs)  # type: ignore[arg-type]
    return annotate_output_schema(schema, build_agent_projection(_Invoice))


def test_hidden_properties_leave_schema_and_required() -> None:
    schema = _annotated()

    assert "etag" not in schema["properties"]
    assert "etag" not in schema["required"]
    assert "id" in schema["properties"]


def test_handle_descriptions() -> None:
    properties = _annotated()["properties"]

    assert properties["id"]["description"] == "Invoice handle."
    # A handle with no declared wording still says what it is.
    assert properties["customer"]["description"] == HANDLE_DESCRIPTION
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
        a = serializers.CharField(style={AGENT: AgentField.hidden()})

    schema = annotate_output_schema(
        output_to_json_schema(_AllHidden), build_agent_projection(_AllHidden)
    )

    assert schema["properties"] == {}
    assert "required" not in schema


def test_none_and_empty_projection_pass_through() -> None:
    class _Plain(serializers.Serializer):
        name = serializers.CharField()

    empty = build_agent_projection(_Plain)
    assert annotate_output_schema(None, empty) is None

    schema = output_to_json_schema(_Plain)
    assert annotate_output_schema(schema, empty) is schema


def test_schema_without_properties_is_returned_as_is() -> None:
    projection = build_agent_projection(_Invoice)
    schema = {"type": "string"}

    assert annotate_output_schema(schema, projection) == {"type": "string"}


class TestSpokenChoiceSchemas:
    """A substituted choice must be *described* in the values it now carries."""

    def test_a_labelled_choice_is_redeclared_in_display_values(self) -> None:
        class _Order(serializers.Serializer):
            status = serializers.ChoiceField(
                choices=[("PENDING_REVIEW", "Awaiting review"), ("PAID", "Paid")]
            )

        projection = build_agent_projection(_Order)
        schema = annotate_output_schema(output_to_json_schema(_Order), projection)

        assert schema["properties"]["status"] == {
            "oneOf": [{"const": "Awaiting review"}, {"const": "Paid"}]
        }

    def test_a_handle_keeps_its_constants(self) -> None:
        class _Order(serializers.Serializer):
            kind = serializers.ChoiceField(
                choices=[("PENDING_REVIEW", "Awaiting review")],
                style={AGENT: AgentField.handle()},
            )

        projection = build_agent_projection(_Order)
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
        projection = build_agent_projection(_Order)
        schema = output_to_json_schema(_Order, registry=registry)
        properties = annotate_output_schema(schema, projection)["properties"]

        assert properties["listed"] == {"enum": ["Awaiting review", "Paid"]}
        # Nothing enum-shaped to rewrite; the rule is respected as written.
        assert properties["opaque"] == {"type": "string"}
