"""``serializer_to_json_schema`` — input-side JSON Schema for a serializer."""

from __future__ import annotations

import dataclasses
from typing import Any

from rest_framework import serializers

from rest_framework_services.jsonschema.utils import dataclass_to_schema, serializer_to_schema
from rest_framework_services.types.json_schema_registry import (
    DEFAULT_JSON_SCHEMA_REGISTRY,
    JsonSchemaRegistry,
)


def serializer_to_json_schema(
    serializer: type | None,
    *,
    partial: bool = False,
    registry: JsonSchemaRegistry = DEFAULT_JSON_SCHEMA_REGISTRY,
) -> dict[str, Any]:
    """Build a JSON Schema *object* for an input serializer / dataclass / ``None``.

    Accepts a DRF ``Serializer`` subclass, a bare ``@dataclass`` type (the
    convention drf-services services use for ``data``), or ``None`` (the
    operation takes no input). Always returns an object — ``{"type": "object"}``
    is the convention for "no declared fields", so an alternate transport can
    still describe the tool.

    ``partial=True`` drops the ``required`` list — mirroring ``spec.partial``,
    where the validator accepts omitted fields, so advertising them as required
    would make schema-strict consumers reject calls the service accepts.

    ``registry`` supplies consumer rules for custom field / Python types — see
    [`JsonSchemaRegistry`][rest_framework_services.types.json_schema_registry.JsonSchemaRegistry].
    """
    schema: dict[str, Any]
    if serializer is None:
        schema = {"type": "object"}
    elif isinstance(serializer, type) and issubclass(serializer, serializers.Serializer):
        schema = serializer_to_schema(serializer(), registry)
    elif isinstance(serializer, type) and dataclasses.is_dataclass(serializer):
        schema = dataclass_to_schema(serializer, registry)
    else:
        schema = {"type": "object"}
    if partial:
        schema.pop("required", None)
    return schema
