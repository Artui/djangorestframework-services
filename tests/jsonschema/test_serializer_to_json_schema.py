"""Tests for ``serializer_to_json_schema``."""

from __future__ import annotations

import dataclasses

from rest_framework import serializers

from rest_framework_services.jsonschema.serializer_to_json_schema import serializer_to_json_schema


class _S(serializers.Serializer):
    name = serializers.CharField()
    count = serializers.IntegerField(required=False)


@dataclasses.dataclass
class _DC:
    name: str
    count: int = 0


def test_none_is_empty_object() -> None:
    assert serializer_to_json_schema(None) == {"type": "object"}


def test_serializer_subclass_is_walked() -> None:
    schema = serializer_to_json_schema(_S)
    assert schema["properties"]["name"] == {"type": "string"}
    assert schema["required"] == ["name"]


def test_dataclass_is_walked() -> None:
    schema = serializer_to_json_schema(_DC)
    assert schema["properties"]["count"] == {"type": "integer"}
    assert schema["required"] == ["name"]


def test_other_type_is_empty_object() -> None:
    class Plain: ...

    assert serializer_to_json_schema(Plain) == {"type": "object"}


def test_partial_drops_required() -> None:
    schema = serializer_to_json_schema(_S, partial=True)
    assert "required" not in schema
    assert schema["properties"]["name"] == {"type": "string"}


def test_registry_is_forwarded_to_fields() -> None:
    from rest_framework_services.types.json_schema_registry import DEFAULT_JSON_SCHEMA_REGISTRY

    class _MoneyField(serializers.Field): ...

    class _Order(serializers.Serializer):
        total = _MoneyField()

    registry = DEFAULT_JSON_SCHEMA_REGISTRY.extend(
        fields=[(_MoneyField, {"type": "string", "format": "money"})]
    )
    schema = serializer_to_json_schema(_Order, registry=registry)
    assert schema["properties"]["total"] == {"type": "string", "format": "money"}


class _ContextAware(serializers.Serializer):
    """A serializer whose field set depends on who is asking.

    Routine over HTTP, where DRF's context always carries a request -- and the
    reason ``build_audience_projection`` synthesizes the same baseline before it
    instantiates. Schema generation instantiated bare and raised ``KeyError``
    on the identical serializer, so a spec could be dispatched and rendered but
    not described.
    """

    def get_fields(self) -> dict[str, serializers.Field]:
        fields = super().get_fields()
        request = self.context["request"]
        fields["title"] = serializers.CharField()
        if getattr(request, "is_staff", False):
            fields["internal_note"] = serializers.CharField()
        return fields


def test_serializer_reading_request_context_is_described() -> None:
    schema = serializer_to_json_schema(_ContextAware)

    assert schema["properties"]["title"] == {"type": "string"}


def test_context_carries_drfs_own_keys() -> None:
    class _ReadsView(serializers.Serializer):
        def get_fields(self) -> dict[str, serializers.Field]:
            fields = super().get_fields()
            # Present and ``None``, exactly as off-HTTP dispatch renders it --
            # a ``KeyError`` here would be the same defect one key over.
            assert self.context["view"] is None
            assert self.context["format"] is None
            fields["ok"] = serializers.BooleanField()
            return fields

    assert serializer_to_json_schema(_ReadsView)["properties"]["ok"] == {"type": "boolean"}


def test_label_travels_as_title() -> None:
    class _Labelled(serializers.Serializer):
        vat_id = serializers.CharField(label="VAT registration number")

    assert serializer_to_json_schema(_Labelled)["properties"]["vat_id"]["title"] == (
        "VAT registration number"
    )


def test_the_label_drf_derives_from_the_name_is_not_repeated() -> None:
    class _Plain(serializers.Serializer):
        vat_id = serializers.CharField()

    # DRF binds ``label="Vat id"`` to every unlabelled field. Emitting that
    # restates the property name in worse English and costs a reader tokens to
    # learn nothing.
    assert "title" not in serializer_to_json_schema(_Plain)["properties"]["vat_id"]


class _NestedIn(serializers.Serializer):
    inner = _S()


class TestMaxDepth:
    def test_unset_describes_the_nested_serializer(self) -> None:
        schema = serializer_to_json_schema(_NestedIn)

        assert schema["properties"]["inner"]["properties"]["name"] == {"type": "string"}

    def test_the_bound_truncates_the_nested_serializer(self) -> None:
        assert serializer_to_json_schema(_NestedIn, max_depth=1) == {
            "type": "object",
            "properties": {"inner": {"type": "object"}},
            "required": ["inner"],
        }

    def test_the_bound_composes_with_partial(self) -> None:
        """``partial`` drops ``required``; the bound is about depth, not fields."""
        assert serializer_to_json_schema(_NestedIn, partial=True, max_depth=1) == {
            "type": "object",
            "properties": {"inner": {"type": "object"}},
        }
