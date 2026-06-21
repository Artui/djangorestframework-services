"""Tests for ``output_to_json_schema``."""

from __future__ import annotations

import dataclasses

from rest_framework import serializers

from rest_framework_services.jsonschema.output_to_json_schema import output_to_json_schema
from rest_framework_services.types.selector_kind import SelectorKind


class _Out(serializers.Serializer):
    id = serializers.IntegerField()


@dataclasses.dataclass
class _OutDC:
    id: int


_ITEM = {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}


def test_none_serializer_yields_none() -> None:
    assert output_to_json_schema(None) is None


def test_unsupported_type_yields_none() -> None:
    class Plain: ...

    assert output_to_json_schema(Plain) is None


def test_retrieve_or_default_kind_is_bare_item() -> None:
    assert output_to_json_schema(_Out) == _ITEM
    assert output_to_json_schema(_Out, kind=SelectorKind.RETRIEVE) == _ITEM


def test_dataclass_serializer_is_walked() -> None:
    assert output_to_json_schema(_OutDC) == _ITEM


def test_list_without_pagination_is_array() -> None:
    assert output_to_json_schema(_Out, kind=SelectorKind.LIST) == {
        "type": "array",
        "items": _ITEM,
    }


def test_list_with_pagination_is_envelope() -> None:
    assert output_to_json_schema(_Out, kind=SelectorKind.LIST, paginate=True) == {
        "type": "object",
        "properties": {
            "items": {"type": "array", "items": _ITEM},
            "page": {"type": "integer"},
            "totalPages": {"type": "integer"},
            "hasNext": {"type": "boolean"},
        },
        "required": ["items", "page", "totalPages", "hasNext"],
    }
