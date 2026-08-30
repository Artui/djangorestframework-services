"""``output_to_json_schema`` — output-side JSON Schema, LIST/pagination aware."""

from __future__ import annotations

import dataclasses
from typing import Any

from rest_framework import serializers

from rest_framework_services.audience.annotate_output_schema import annotate_output_schema
from rest_framework_services.jsonschema.utils import (
    dataclass_to_schema,
    serializer_for_schema,
    serializer_to_schema,
)
from rest_framework_services.types.audience_projection import AudienceProjection
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
    projection: AudienceProjection | None = None,
    handle_description: str | None = None,
    registry: JsonSchemaRegistry = DEFAULT_JSON_SCHEMA_REGISTRY,
    max_depth: int | None = None,
) -> dict[str, Any] | None:
    """Build a JSON Schema for an output serializer, or ``None`` when undeclared.

    Returns ``None`` when there is no ``output_serializer`` — callers shouldn't
    fabricate a misleading shape. ``kind`` / ``paginate`` make the schema match
    what dispatch actually returns:

    - ``kind=None`` / ``RETRIEVE`` — the bare item schema.
    - ``kind=LIST, paginate=False`` — ``{type: array, items: <item>}``.
    - ``kind=LIST, paginate=True`` — the pagination envelope
      ``{items, page, totalPages, hasNext}``.

    ``projection`` applies the serializer's field markings, mirroring what
    [`project_payload`][rest_framework_services.audience.project_payload.project_payload]
    does to the payload. It lands on the **item**, wherever the item sits for this
    ``kind`` — the array wrapper and the pagination envelope are this function's
    own shapes and belong to no serializer, so a projection walking them would
    look for markings that cannot exist and silently annotate nothing.

    ``handle_description`` is passed through to
    [`annotate_output_schema`][rest_framework_services.audience.annotate_output_schema.annotate_output_schema]
    as the fallback wording for an unlabelled handle. It defaults to nothing:
    what a reader should *do* with an identifier depends on the reader, and the
    transport is what knows.

    ``registry`` supplies consumer rules for custom field / Python types — see
    [`JsonSchemaRegistry`][rest_framework_services.types.json_schema_registry.JsonSchemaRegistry].

    ``max_depth`` bounds how many serializer levels the **item** describes,
    truncating deeper ones to ``{"type": "object"}``; ``None``, the default,
    describes them all. The item is level 1, and the array wrapper and the
    pagination envelope are this function's own shapes, so they cost no level.
    Independently of this bound, a serializer that nests itself is truncated at
    the re-entry rather than recursing until the process dies. Truncation is
    flat and self-contained — never ``$defs`` / ``$ref``, which most MCP clients
    reject outright.
    """
    item_schema: dict[str, Any] | None = _item_schema(output_serializer, registry, max_depth)
    if item_schema is None:
        return None
    if projection is not None:
        item_schema = (
            annotate_output_schema(item_schema, projection, handle_description=handle_description)
            or item_schema
        )
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
    output_serializer: type | None, registry: JsonSchemaRegistry, max_depth: int | None
) -> dict[str, Any] | None:
    if output_serializer is None:
        return None
    if isinstance(output_serializer, type) and issubclass(
        output_serializer, serializers.Serializer
    ):
        return serializer_to_schema(
            serializer_for_schema(output_serializer), registry, for_output=True, max_depth=max_depth
        )
    if isinstance(output_serializer, type) and dataclasses.is_dataclass(output_serializer):
        return dataclass_to_schema(output_serializer, registry)
    return None
