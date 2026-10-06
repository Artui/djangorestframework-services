"""Tests for mirroring an agent projection onto a JSON Schema."""

from __future__ import annotations

from typing import Any

import pytest
from jsonschema import Draft202012Validator
from rest_framework import serializers

from rest_framework_services.audience.annotate_output_schema import annotate_output_schema
from rest_framework_services.audience.build_audience_projection import build_audience_projection
from rest_framework_services.audience.project_payload import project_payload
from rest_framework_services.jsonschema.output_to_json_schema import output_to_json_schema
from rest_framework_services.types.audience_projection import AudienceProjection
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
            # The choice values are strings and the spoken labels replacing them
            # are too, so the declared type survives the substitution.
            "type": "string",
            "oneOf": [{"const": "Awaiting review"}, {"const": "Paid"}],
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

    def test_a_nullable_choice_described_as_a_union_is_redeclared(self) -> None:
        """An ``X | None`` annotation is described as ``anyOf``, the shape a
        dataclass output's optional ``Enum`` field takes. Each member is rewritten,
        so the null branch survives and the enum branch speaks the labels."""
        schema = {
            "type": "object",
            "properties": {"tier": {"anyOf": [{"enum": ["gold"]}, {"type": "null"}]}},
        }
        projection = AudienceProjection(choice_labels={"tier": {"gold": "Gold"}})

        assert annotate_output_schema(schema, projection)["properties"]["tier"] == {
            "anyOf": [{"enum": ["Gold"]}, {"type": "null"}]
        }

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


LOW: dict[Any, str] = {1: "Low"}


def _spoken(subschema: dict[str, Any], labels: dict[Any, str] = LOW) -> Any:
    schema = {"type": "object", "properties": {"p": subschema}}
    projection = AudienceProjection(choice_labels={"p": labels})
    return annotate_output_schema(schema, projection)["properties"]["p"]


class TestRestatedType:
    """A display is a string, so a stated ``type`` follows the values now listed.

    Left as the walk stated it, an integer choice spoken as ``"Low"`` is
    described as an integer, and the projected payload fails the projected
    schema.
    """

    def test_an_integer_spoken_whole_is_a_string(self) -> None:
        assert _spoken({"type": "integer", "oneOf": [{"const": 1, "title": "Low"}]}) == {
            "type": "string",
            "oneOf": [{"const": "Low"}],
        }

    def test_a_nullable_integer_keeps_its_null(self) -> None:
        subschema = {"type": ["integer", "null"], "oneOf": [{"const": 1}, {"const": None}]}

        assert _spoken(subschema) == {
            "type": ["string", "null"],
            "oneOf": [{"const": "Low"}, {"const": None}],
        }

    def test_null_is_stated_last_wherever_it_was_listed(self) -> None:
        subschema = {"type": ["null", "integer"], "enum": [None, 1]}

        assert _spoken(subschema) == {"type": ["string", "null"], "enum": [None, "Low"]}

    def test_null_is_kept_where_the_stated_type_admitted_it(self) -> None:
        """Here the null is admitted by an entry with no constant rather than
        listed as one, and it is still served, so the restated type must not
        refuse it."""
        subschema = {
            "type": ["integer", "null"],
            "oneOf": [{"const": 1, "title": "Low"}, {"type": "null"}],
        }

        assert _spoken(subschema)["type"] == ["string", "null"]

    @pytest.mark.parametrize(
        ("subschema", "expected"),
        [
            (
                {
                    "type": ["integer", "null"],
                    "oneOf": [{"const": 1, "title": "Low"}, {"const": None, "title": "Unknown"}],
                },
                {"type": "string", "oneOf": [{"const": "Low"}, {"const": "Unknown"}]},
            ),
            (
                {"type": ["null", "integer"], "enum": [None, 1]},
                {"type": "string", "enum": ["Unknown", "Low"]},
            ),
        ],
        ids=["one-of", "enum"],
    )
    def test_a_null_spoken_as_a_label_is_not_named(
        self, subschema: dict[str, Any], expected: dict[str, Any]
    ) -> None:
        """Django's ``(None, "Unknown")``: the stated type admitted a null, and
        the payload serves ``"Unknown"`` in its place, so the restated type
        names no null it never serves. The listed displays refused a null
        before the type stopped naming one, so what the schema accepts is
        unchanged."""
        assert _spoken(subschema, {**LOW, None: "Unknown"}) == expected
        assert not Draft202012Validator({**expected, "type": ["string", "null"]}).is_valid(None)
        assert Draft202012Validator(expected).is_valid("Unknown")

    def test_a_type_stated_as_null_alone_still_admits_it(self) -> None:
        """A scalar ``"null"`` is a stated type admitting null, as a list
        naming it is."""
        subschema = {"type": "null", "enum": [None, 1]}

        assert _spoken(subschema) == {"type": ["string", "null"], "enum": [None, "Low"]}

    def test_a_type_that_refused_null_still_refuses_it(self) -> None:
        """The restated type admits what the stated one did, in display terms,
        and nothing more."""
        subschema = {"type": "integer", "enum": [1, None]}

        assert _spoken(subschema) == {"type": "string", "enum": ["Low", None]}

    def test_a_value_left_unspoken_keeps_its_type_beside_the_string(self) -> None:
        subschema = {"type": "integer", "enum": [1, 2]}

        assert _spoken(subschema) == {"type": ["string", "integer"], "enum": ["Low", 2]}

    @pytest.mark.parametrize(
        ("value", "expected"),
        [(True, "boolean"), (2.5, "number"), (3, "integer"), ("x", "string")],
    )
    def test_each_json_type_is_named_as_json_names_it(self, value: Any, expected: str) -> None:
        assert _spoken({"type": "string", "enum": [value]}, {"other": "Other"})["type"] == expected

    def test_a_value_json_has_no_scalar_type_for_leaves_the_type_as_stated(self) -> None:
        """Only a type it can name is restated; anything else is left as written."""
        subschema = {"type": "array", "enum": [(1, 2)]}

        assert _spoken(subschema) == subschema

    def test_only_a_null_left_to_type_leaves_the_type_as_stated(self) -> None:
        """Nothing but ``None`` is listed and the stated type refused it, so
        there is no type to restate it as."""
        subschema = {"type": "integer", "enum": [None]}

        assert _spoken(subschema) == subschema

    def test_a_one_of_with_no_constant_keeps_its_type(self) -> None:
        subschema = {"type": ["string", "null"], "oneOf": [{"pattern": "^a"}]}

        assert _spoken(subschema) == subschema

    def test_a_choice_listing_nothing_keeps_its_type(self) -> None:
        """Nothing listed to restate the type from. Nullable, so that restating
        it from nothing would leave ``"null"`` alone in place of the type."""
        subschema = {"type": ["string", "null"], "enum": []}

        assert _spoken(subschema) == subschema

    def test_an_entry_admitting_more_than_its_constants_keeps_the_type(self) -> None:
        """An entry with no ``const`` admits values the displays say nothing
        about, so narrowing the type to the displays' would refuse them: ``7``
        matched ``minimum`` and was an integer, and would no longer be one of
        the types stated. Left as written, the type refuses the display
        ``"Low"`` a row holding ``1`` is served, so that row still fails this
        schema; only a schema written by hand reaches this shape, and narrowing
        would refuse ``7`` and the null as well."""
        subschema = {
            "type": ["integer", "null"],
            "oneOf": [{"const": 1, "title": "Low"}, {"minimum": 5}],
        }

        spoken = _spoken(subschema)

        assert spoken == {
            "type": ["integer", "null"],
            "oneOf": [{"const": "Low"}, {"minimum": 5}],
        }
        assert Draft202012Validator(spoken).is_valid(7)
        assert Draft202012Validator(spoken).is_valid(None)
        assert not Draft202012Validator(spoken).is_valid("Low")

    def test_an_untyped_choice_states_no_type(self) -> None:
        """Nothing was claimed, so there is nothing to contradict."""
        assert _spoken({"enum": [1]}) == {"enum": ["Low"]}


class _Typed(serializers.ChoiceField):
    """A choice field a registry rule describes with its type stated."""


class TestServedNull:
    """``"null"`` is named exactly where the projected payload can still serve one.

    A nullable field's ``None`` is served as its display where the choices give
    it one, and as a null where they do not. The walk states no type beside a
    choice listing ``None``, so the restated type meets a null only where a
    rule states one, as these do.
    """

    @staticmethod
    def projected(
        choices: list[tuple[Any, str]], rule: dict[str, Any] | None = None
    ) -> tuple[Any, Any]:
        class _Row(serializers.Serializer):
            p = _Typed(choices=choices, allow_null=True)

        registry = DEFAULT_JSON_SCHEMA_REGISTRY
        if rule is not None:
            registry = registry.extend(fields=[(_Typed, rule)])
        projection = build_audience_projection(_Row)
        schema: Any = output_to_json_schema(_Row, projection=projection, registry=registry)
        served = project_payload(dict(_Row(instance={"p": None}).data), projection)
        Draft202012Validator(schema).validate(served)
        return schema["properties"]["p"], served["p"]

    def test_a_null_with_a_display_is_served_as_it_and_never_named(self) -> None:
        schema, served = self.projected(
            [(None, "Unknown"), (1, "Low")],
            {
                "type": ["integer", "null"],
                "oneOf": [{"const": None, "title": "Unknown"}, {"const": 1, "title": "Low"}],
            },
        )

        assert served == "Unknown"
        assert schema == {"type": "string", "oneOf": [{"const": "Unknown"}, {"const": "Low"}]}

    def test_a_null_with_no_display_is_served_and_still_named(self) -> None:
        schema, served = self.projected(
            [(1, "Low")],
            {"type": ["integer", "null"], "oneOf": [{"const": 1, "title": "Low"}, {"const": None}]},
        )

        assert served is None
        assert schema == {"type": ["string", "null"], "oneOf": [{"const": "Low"}, {"const": None}]}

    def test_the_walk_states_no_type_beside_a_listed_null(self) -> None:
        """With no rule, the walk's own schema for the same field: the null's
        display is listed, and there is no stated type to name a null in."""
        schema, served = self.projected([(None, "Unknown"), (1, "Low")])

        assert served == "Unknown"
        assert schema == {"oneOf": [{"const": "Unknown"}, {"const": "Low"}]}


class TestSharedDisplays:
    """Django lets two values share one display, and a reader is told it once.

    Listed twice, a ``oneOf`` matches a row served that display under both
    entries, and ``oneOf`` admits only a value valid under exactly one.
    """

    LABELS: dict[Any, str] = {"live": "Published", "legacy": "Draft", "draft": "Draft"}

    def test_an_enum_lists_each_display_once_in_first_seen_order(self) -> None:
        subschema = {"type": "string", "enum": ["live", "legacy", "draft"]}

        assert _spoken(subschema, self.LABELS) == {
            "type": "string",
            "enum": ["Published", "Draft"],
        }

    def test_a_one_of_lists_each_display_once(self) -> None:
        subschema = {
            "oneOf": [
                {"const": "legacy", "title": "Draft"},
                {"const": "draft", "title": "Draft"},
                {"const": "live", "title": "Published"},
            ]
        }

        assert _spoken(subschema, self.LABELS) == {
            "oneOf": [{"const": "Draft"}, {"const": "Published"}]
        }

    def test_an_array_of_shared_displays_stops_claiming_unique_items(self) -> None:
        subschema = {"type": "array", "items": {"enum": ["legacy", "draft"]}, "uniqueItems": True}

        assert _spoken(subschema, self.LABELS) == {"type": "array", "items": {"enum": ["Draft"]}}

    def test_an_array_of_distinct_displays_keeps_unique_items(self) -> None:
        """The condition of the drop: no two values collapsed, so the displays
        served for distinct values are distinct too."""
        subschema = {"type": "array", "items": {"enum": ["legacy", "live"]}, "uniqueItems": True}

        assert _spoken(subschema, self.LABELS) == {
            "type": "array",
            "items": {"enum": ["Draft", "Published"]},
            "uniqueItems": True,
        }

    def test_a_one_of_member_with_no_constant_keeps_its_place(self) -> None:
        subschema = {"oneOf": [{"type": "null"}, {"const": "live", "title": "Published"}]}

        assert _spoken(subschema, self.LABELS) == {
            "oneOf": [{"type": "null"}, {"const": "Published"}]
        }

    def test_a_boolean_is_not_the_number_python_says_it_equals(self) -> None:
        """``True == 1`` in Python and not in JSON, so both stay listed and a
        row served either still matches."""
        assert _spoken({"enum": [True, 1, "x"]}, {"x": "Ex"}) == {"enum": [True, 1, "Ex"]}

    def test_a_boolean_and_the_number_it_equals_stay_two_constants(self) -> None:
        """The ``oneOf`` spelling of the test above, the one a duplicate breaks."""
        subschema = {"oneOf": [{"const": True}, {"const": 1}, {"const": "x", "title": "Ex"}]}

        assert _spoken(subschema, {"x": "Ex"}) == {
            "oneOf": [{"const": True}, {"const": 1}, {"const": "Ex"}]
        }


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
