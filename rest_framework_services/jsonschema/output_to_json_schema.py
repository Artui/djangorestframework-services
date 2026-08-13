"""``output_to_json_schema`` — output-side JSON Schema, LIST/pagination aware."""

from __future__ import annotations

import dataclasses
from typing import Any

from rest_framework import serializers

from rest_framework_services.jsonschema.utils import dataclass_to_schema, serializer_to_schema
from rest_framework_services.types.json_schema_registry import (
    DEFAULT_JSON_SCHEMA_REGISTRY,
    JsonSchemaRegistry,
)
from rest_framework_services.types.selector_kind import SelectorKind


def output_to_json_schema(
    output_serializer: type | None,
    *,
    kind: SelectorKind | None = None,
    paginate: bool = False,
    registry: JsonSchemaRegistry = DEFAULT_JSON_SCHEMA_REGISTRY,
) -> dict[str, Any] | None:
    """Build a JSON Schema for an output serializer, or ``None`` when undeclared.

    Returns ``None`` when there is no ``output_serializer`` — callers shouldn't
    fabricate a misleading shape. ``kind`` / ``paginate`` make the schema match
    what dispatch actually returns:

    - ``kind=None`` / ``RETRIEVE`` — the bare item schema.
    - ``kind=LIST, paginate=False`` — ``{type: array, items: <item>}``.
    - ``kind=LIST, paginate=True`` — the pagination envelope
      ``{items, page, totalPages, hasNext}``.

    ``registry`` supplies consumer rules for custom field / Python types — see
    [`JsonSchemaRegistry`][rest_framework_services.types.json_schema_registry.JsonSchemaRegistry].
    """
    item_schema: dict[str, Any] | None = _item_schema(output_serializer, registry)
    if item_schema is None:
        return None
    if kind is not SelectorKind.LIST:
        return item_schema
    array_schema: dict[str, Any] = {"type": "array", "items": item_schema}
    if not paginate:
        return array_schema
    return {
        "type": "object",
        "properties": {
            "items": array_schema,
            "page": {"type": "integer"},
            "totalPages": {"type": "integer"},
            "hasNext": {"type": "boolean"},
        },
        "required": ["items", "page", "totalPages", "hasNext"],
    }


def _item_schema(
    output_serializer: type | None, registry: JsonSchemaRegistry
) -> dict[str, Any] | None:
    if output_serializer is None:
        return None
    if isinstance(output_serializer, type) and issubclass(
        output_serializer, serializers.Serializer
    ):
        return serializer_to_schema(output_serializer(), registry)
    if isinstance(output_serializer, type) and dataclasses.is_dataclass(output_serializer):
        return dataclass_to_schema(output_serializer, registry)
    return None
