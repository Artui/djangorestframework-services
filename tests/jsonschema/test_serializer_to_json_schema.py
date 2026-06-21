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
